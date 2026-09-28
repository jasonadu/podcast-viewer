#!/usr/bin/env python3
"""
多线程 YouTube 视频下载器 (yt-dlp + aria2c).

- 下载引擎: yt-dlp 解析/元数据 + aria2c 多连接分块传输 (-x16 -s16 -k1M),
  音视频合并依赖 ffmpeg (PATH 中已安装).
- 支持三种输入: 单个/多个命令行 URL、URL 列表文件 (-f, 每行一个)、播放列表 URL.
- 并发策略 (简单模式): 默认 workers=2, 需要提速时手动 --workers 5.
  注意: 429 限流主要发生在元数据提取阶段 (访问 youtube.com),
  提高 workers 会更快触发限流; 配合 download-archive 可中断后重跑自动跳过.
- 归档预检查 (两级, 都发生在进入线程池之前): ① 单个视频的 id 直接从 URL
  里取 (不联网), 归档中已有就立刻 [SKIP]; ② 其余条目 (播放列表展开后)
  解析完再统一判定, 同样打印 [SKIP]. 已归档的既不占用 worker 名额,
  也不会拖到下载阶段才发现"归档中已存在".
- 进度可见性: 4 个阶段 ([1/4]~[4/4]) 都有编号进度; 解析/下载这类阻塞步骤
  超过 HEARTBEAT_INTERVAL 秒会打印"仍在进行 + 已用时间 + 临时文件大小"心跳;
  yt-dlp 的 info/warning/error 会带 worker 标签转发出来.
  (aria2c 作为外部下载器只在结束时回调一次, 没有增量进度,
  下载期间的心跳就是唯一的信息来源, 否则看起来就像卡死.)

用法:
  python -m subtitle_download.download_video <URL> [URL2 ...]      # 单个或多个
  python -m subtitle_download.download_video -f urls.txt           # 列表文件
  python -m subtitle_download.download_video -f urls.txt --workers 5
  python -m subtitle_download.download_video "https://www.youtube.com/playlist?list=..."
  python -m subtitle_download.download_video <URL> --audio-only    # 提取 mp3

常用参数:
  --workers N              并发线程数 (默认 2)
  --quality 1080           最大高度, 0 表示最佳 (默认 1080)
  --audio-only             仅提取音频 (mp3)
  --output-dir DIR         输出目录 (默认 ./videos)
  --aria2c PATH            自定义 aria2c 路径
  --cookies FILE / --cookies-from-browser chrome   年龄限制/会员视频
"""
import argparse
import os
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from shutil import which as shutil_which

import yt_dlp

# --- aria2c 配置 (与 download_podcasts_se_radio.py 保持一致) ---
ARIA2C_DEFAULT_PATH = r"D:\Tools\aria2-1.37.0-win-64bit-build1\aria2c.exe"
ARIA2C_SPLIT = 16          # 单文件分块数
ARIA2C_MAX_CONN = 16       # 单服务器最大连接数
ARIA2C_MIN_SPLIT = "1M"    # 最小分块大小

DEFAULT_WORKERS = 2
DEFAULT_OUTPUT_DIR = "videos"
DEFAULT_QUALITY = 1080

# 任务启动随机间隔 (秒), 错峰减轻 429
START_JITTER = (3, 8)
# 429 指数退避: 重试次数与初始等待
RETRY_MAX = 3
RETRY_BASE_WAIT = 8

# 单任务进度行最多每 N 秒打印一次 (避免刷屏)
PROGRESS_INTERVAL = 5
# 解析/下载等阻塞步骤的心跳间隔 (秒): 超过就打印一次"仍在进行"
HEARTBEAT_INTERVAL = 15
# 未完成的临时文件后缀 (yt-dlp 的 .part/.ytdl, aria2c 的 .aria2)
TEMP_SUFFIXES = ('.part', '.ytdl', '.aria2', '.temp')
# 归档行格式为 '<extractor_key> <video_id>', 缺省按 youtube 处理
DEFAULT_IE_KEY = 'youtube'
# yt-dlp 的高频噪音行 (分片/进度重绘), 进度由我们自己的进度行与心跳负责
_NOISE_PREFIXES = ('[hls @', '[dash @')
# 从 URL 里直接取 YouTube 视频 id (纯字符串操作, 不联网)
_YOUTUBE_ID_RE = re.compile(
    r'(?:youtu\.be/|[?&]v=|/v/|/vi/|/embed/|/shorts/|/live/)'
    r'([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])')

# 线程安全的输出锁
_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


# ---------------------------------------------------------------------------
# 进度可见性辅助: 一行式格式化 / 心跳线程 / yt-dlp 日志转发
# ---------------------------------------------------------------------------
def clip(text, max_len: int = 140) -> str:
    """压掉换行与多余空白并截断, 保证日志始终是一行且不过长."""
    s = ' '.join(str(text).split())
    return s if len(s) <= max_len else s[:max_len - 3] + '...'


def fmt_bytes(n) -> str:
    """字节数格式化成可读文本: 1234567 -> '1.2MiB'."""
    if n is None:
        return '?'
    value = float(n)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f"{int(value)}{unit}" if unit == 'B' else f"{value:.1f}{unit}"
        value /= 1024


def fmt_duration(sec: float) -> str:
    """秒数格式化: '45.2s' / '1m23s'."""
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.1f}s"
    return f"{int(sec // 60)}m{int(sec % 60):02d}s"


def fmt_eta(sec) -> str:
    """剩余秒数格式化成 mm:ss, 未知返回 '?'."""
    if not sec or sec < 0:
        return '?'
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def format_progress(d: dict) -> str:
    """把 yt-dlp 的进度字典格式化成一行: '45.3% 12.4MiB/27.4MiB 2.1MiB/s ETA 00:07'."""
    done = d.get('downloaded_bytes') or 0
    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
    if total:
        pct = f"{done * 100.0 / total:5.1f}%"
        size = f"{fmt_bytes(done)}/{fmt_bytes(total)}"
    else:
        pct = '  ?  '
        size = fmt_bytes(done)
    speed = d.get('speed')
    return (f"{pct} {size} {(fmt_bytes(speed) + '/s') if speed else '?'} "
            f"ETA {fmt_eta(d.get('eta'))}")


def temp_size_str(output_dir: str) -> str:
    """统计输出目录里未完成临时文件的总大小 (aria2c/yt-dlp 正在下载的分片)."""
    total = 0
    try:
        with os.scandir(output_dir) as entries:
            for entry in entries:
                if entry.name.endswith(TEMP_SUFFIXES) and entry.is_file():
                    total += entry.stat().st_size
    except OSError:
        return '?'
    return fmt_bytes(total)


class Heartbeat:
    """周期性打印"仍在进行"的心跳, 免得长时间静默看起来像卡死.

    用法: `with Heartbeat("解析播放列表"): ...`. 退出上下文时自动停止线程.
    detail 是可选回调, 用来附加实时信息 (例如下载中临时文件的合计大小).
    """

    def __init__(self, desc: str, interval: float = HEARTBEAT_INTERVAL, detail=None):
        self.desc = desc
        self.interval = interval
        self.detail = detail
        self._start = 0.0
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._start = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False

    def elapsed(self) -> float:
        return time.time() - self._start

    def _beat(self):
        while not self._stop.wait(self.interval):
            extra = ''
            if self.detail is not None:
                try:
                    extra = f", {self.detail()}"
                except Exception:
                    extra = ''
            log(f"[info] ...{self.desc} 仍在进行, 已用 {int(self.elapsed())}s{extra}")


def is_noise(msg: str) -> bool:
    """yt-dlp 的高频重复行 (分片进度/进度重绘): 转发时丢掉, 免得刷屏."""
    s = str(msg).strip()
    if s.startswith(_NOISE_PREFIXES):
        return True
    return s.startswith('[download]') and ('% of' in s or ' at ' in s)


class YdlLogger:
    """把 yt-dlp 内部消息转发到统一输出 (带 worker/阶段标签).

    quiet=True 时 yt-dlp 自己不再打印, 但 warning/error 会交给 logger,
    于是"限流重试/网络重试/格式合并/删除中间文件"这些原本静默的步骤
    都会变成可见的进度信息.
    """

    def __init__(self, prefix: str = '', max_len: int = 140):
        self.prefix = prefix
        self.max_len = max_len

    def debug(self, msg):
        # yt-dlp 用 '[debug] ' 前缀标记调试信息, 其余是 to_screen 的普通信息
        if not str(msg).startswith('[debug] ') and not is_noise(msg):
            log(f"{self.prefix}{clip(msg, self.max_len)}")

    def info(self, msg):
        log(f"{self.prefix}{clip(msg, self.max_len)}")

    def warning(self, msg):
        log(f"{self.prefix}[warn] {clip(msg, self.max_len)}")

    def error(self, msg):
        log(f"{self.prefix}[error] {clip(msg, self.max_len)}")


def find_aria2c(custom_path: str = "") -> str:
    """查找 aria2c 可执行文件. 优先自定义路径, 其次默认路径, 最后 PATH."""
    candidates = []
    if custom_path:
        candidates.append(custom_path)
    candidates.append(ARIA2C_DEFAULT_PATH)
    for name in ("aria2c.exe", "aria2c"):
        candidates.append(name)
    for c in candidates:
        if os.sep in c or "/" in c:
            if os.path.isfile(c):
                return c
        else:
            if shutil_which(c):
                return c
    return ""


def probe_aria2c(path: str) -> bool:
    """运行 aria2c --version 确认可用."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def parse_cookies_opts(args) -> dict:
    if getattr(args, 'cookies', ''):
        return {'cookiefile': args.cookies}
    if getattr(args, 'cookies_from_browser', ''):
        return {'cookiesfrombrowser': (args.cookies_from_browser,)}
    return {}


def expand_playlist(url: str, cookies_opts: dict, label: str = '') -> list:
    """URL → 任务列表 [{'url','name','id','extractor_key'}].

    播放列表只做扁平解析 (extract_flat, 分页由 yt-dlp 处理), 不展开每个视频;
    单个视频会顺带拿到标题和 id —— id 用于进入线程池之前的归档预检查.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': False,     # 交给 YdlLogger 输出, 限流/重试不再静默
        'logger': YdlLogger(f"{label} " if label else ''),
        'extract_flat': 'in_playlist',   # 只取条目, 不解析每个视频的详细流
        'skip_download': True,
        'socket_timeout': 20,     # 网络不通时尽快失败, 不要无限等待 (同 youtube_list.py)
        'retries': 3,
        **cookies_opts,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError(f'yt-dlp 未返回任何信息: {url}')

    if info.get('_type') not in ('playlist', 'multi_video'):
        return [{
            'url': url,
            'name': info.get('title') or url,
            'id': info.get('id') or '',
            'extractor_key': info.get('extractor_key') or info.get('ie_key') or '',
        }]

    entries = info.get('entries') or []
    items = []
    for e in entries:
        if not e:               # 播放列表里可能有取不到的条目 (私享/已删除)
            continue
        # extract_flat 下 id 就是视频 id, 拼 watch URL 最稳妥
        vid_url = e.get('url') or e.get('id') or ''
        if e.get('ie_key') == 'Youtube' and vid_url and not vid_url.startswith('http'):
            vid_url = f"https://www.youtube.com/watch?v={vid_url}"
        if not vid_url:
            continue
        items.append({
            'url': vid_url,
            'name': e.get('title') or vid_url,
            'id': e.get('id') or '',
            'extractor_key': e.get('ie_key') or e.get('extractor_key') or '',
        })
    return items


def expand_playlist_with_retry(url: str, cookies_opts: dict, label: str = '') -> list:
    """解析 URL (含播放列表展开), 对 429 限流做指数退避重试."""
    for attempt in range(1, RETRY_MAX + 2):
        try:
            return expand_playlist(url, cookies_opts, label)
        except Exception as e:
            if is_rate_limit_error(e) and attempt <= RETRY_MAX:
                wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
                log(f"[WARN]  解析 {label} 触发 429 限流, {wait}s 后重试 "
                    f"({attempt}/{RETRY_MAX})")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f'解析重试次数用尽: {url}')   # 理论不可达


# ---------------------------------------------------------------------------
# 归档 (download-archive) 预检查: 在线程池启动之前判定"是否已下载"
# ---------------------------------------------------------------------------
def make_archive_id(extractor_key: str, video_id: str) -> str:
    """生成 yt-dlp 的归档 id (与 yt_dlp.utils.make_archive_id 一致: 小写 + 空格)."""
    key = (extractor_key or DEFAULT_IE_KEY).lower()
    return f"{key} {video_id}"


def youtube_id_from_url(url: str) -> str:
    """从 URL 里直接取 YouTube 视频 id (不联网), 取不到返回 ''.

    支持 youtu.be/<id>、watch?v=<id>、/shorts/<id>、/embed/<id>、/live/<id>.
    """
    m = _YOUTUBE_ID_RE.search(url or '')
    return m.group(1) if m else ''


def is_playlist_url(url: str) -> bool:
    """URL 是否带播放列表参数 (这类必须联网展开, 不能按单个视频跳过)."""
    return 'list=' in (url or '')


def parse_archive_file(archive_path: str) -> tuple:
    """解析归档文件, 返回 (完整行集合, 视频 id 集合).

    yt-dlp 的行格式是 '<extractor_key> <video_id>' (extractor_key 小写),
    这里同时兼容更早的单列 '<video_id>' 格式.
    """
    lines, vid_ids = set(), set()
    if not os.path.isfile(archive_path):
        return lines, vid_ids
    try:
        with open(archive_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                lines.add(line)
                parts = line.split()
                vid_ids.add(parts[1] if len(parts) >= 2 else parts[0])
    except OSError as e:
        log(f"[warn] 读取归档文件失败 {archive_path}: {e}")
    return lines, vid_ids


class ArchiveIndex:
    """归档索引: 复用 yt-dlp 的归档语义, 支持"下载之前"按 video id 判重.

    归档文件非空时持有一个 YoutubeDL 实例以复用 in_download_archive()
    (只读归档集合, 不联网、不下载); 初始化失败回退到本地解析.
    """

    def __init__(self, archive_path: str, lines: set = None, ids: set = None):
        self.path = archive_path
        if lines is None or ids is None:
            lines, ids = parse_archive_file(archive_path)
        self.lines, self.ids = lines, ids
        self._ydl = None
        if self.lines:
            try:
                self._ydl = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True,
                                              'download_archive': archive_path})
            except Exception as e:
                log(f"[warn] yt-dlp 归档检查初始化失败, 改用归档文件本地解析: {e}")

    @property
    def count(self) -> int:
        """归档里的记录条数."""
        return len(self.lines)

    def is_archived(self, video_id: str, extractor_key: str = '',
                    url: str = '') -> bool:
        """video id 是否已在归档中 (优先 yt-dlp 判定, 失败回退本地解析)."""
        if not video_id:
            return False
        if self._ydl is not None:
            try:
                if self._ydl.in_download_archive({
                        'id': video_id,
                        'url': url or None,
                        'extractor_key': extractor_key or None}):
                    return True
            except Exception:
                pass
        return (make_archive_id(extractor_key, video_id) in self.lines
                or video_id in self.ids)


def split_archived(tasks: list, archive: ArchiveIndex) -> tuple:
    """归档预检查 (多线程下载之前完成): 返回 (待下载, 已归档, 无法判断).

    这里兜底处理"解析之后才拿到 id"的条目 (例如播放列表条目);
    解析阶段也拿不到 id 的条目只能退回下载阶段判断
    (download_one 里仍保留"已存在视为跳过"的兜底).
    """
    if archive.count == 0:
        return list(tasks), [], []

    pending, archived, unchecked = [], [], []
    for task in tasks:
        vid = task.get('id') or youtube_id_from_url(task.get('url') or '')
        if not vid:
            unchecked.append(task)
            pending.append(task)
            continue
        if archive.is_archived(vid, task.get('extractor_key') or '',
                               task.get('url') or ''):
            archived.append(task)
        else:
            pending.append(task)
    return pending, archived, unchecked


def build_ydl_opts(args, archive_path: str, aria2c: str) -> dict:
    """构建 yt-dlp 选项, 使用 aria2c 作为外部下载器."""
    if args.audio_only:
        fmt = 'bestaudio/best'
    elif args.quality and args.quality > 0:
        h = args.quality
        fmt = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    else:
        fmt = 'bv*+ba/b'

    opts = {
        'outtmpl': os.path.join(args.output_dir, '%(title)s [%(id)s].%(ext)s'),
        'format': fmt,
        'merge_output_format': 'mp4',   # ffmpeg 合并
        'quiet': True,
        'no_warnings': False,   # 交给 YdlLogger: 限流/重试不再静默
        'noprogress': True,     # 关掉 yt-dlp 自带的进度重绘, 用我们的进度行 + 心跳
        'retries': 3,
        'fragment_retries': 3,
        'noplaylist': True,   # 每个任务只下载单个视频 (播放列表已提前展开)
        'download_archive': archive_path,   # 归档, 中断后重跑自动跳过
        # 注意: logger / progress_hooks 在 download_one 里按任务注入 (带 worker 标签)
    }

    # 关键: 用 aria2c 做多连接分块下载
    if aria2c:
        opts['external_downloader'] = {'default': aria2c}
        opts['external_downloader_args'] = {
            'aria2c': [
                f"--split={ARIA2C_SPLIT}",
                f"--max-connection-per-server={ARIA2C_MAX_CONN}",
                f"--min-split-size={ARIA2C_MIN_SPLIT}",
                "--file-allocation=none",
                "--console-log-level=warn",
                "--summary-interval=0",
            ],
        }

    if args.audio_only:
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    opts.update(parse_cookies_opts(args))
    return opts


def make_progress_hook(label: str, name: str):
    """构造单个任务的进度回调: 最多每 PROGRESS_INTERVAL 秒打印一行 (线程安全).

    注意: aria2c 作为外部下载器只会在结束时回调一次 'finished', 下载过程中
    没有增量回调; 因此下载期间的可见性靠 download_one 里的 Heartbeat.
    """
    state = {'last': 0.0}

    def hook(d):
        try:
            status = d.get('status')
            if status == 'finished':
                log(f"{label} 分片传输完成, 正在合并/后处理: {clip(name, 60)}")
                return
            if status != 'downloading':
                return
            now = time.time()
            if now - state['last'] < PROGRESS_INTERVAL:
                return
            state['last'] = now
            log(f"{label} {format_progress(d)}")
        except Exception:
            pass

    return hook


def is_rate_limit_error(err: Exception) -> bool:
    s = str(err)
    return '429' in s or 'Too Many Requests' in s


def download_one(task: dict, ydl_opts: dict, worker_idx: int, idx: int,
                 total: int, output_dir: str) -> dict:
    """下载单个视频: 429 指数退避重试 + 独立进度回调/心跳.

    返回 {'status': 'ok'|'skip'|'fail', 'name': str, 'error': str}
    """
    url = task['url']
    name = task['name']
    label = f"[worker-{worker_idx}] ({idx}/{total})"
    short = clip(name, 60)

    for attempt in range(1, RETRY_MAX + 2):
        # 每个任务独立 YoutubeDL 实例 + 独立进度回调 (yt-dlp 非线程安全)
        opts = dict(ydl_opts)
        opts['logger'] = YdlLogger(f"{label} ")
        opts['progress_hooks'] = [make_progress_hook(label, name)]

        try:
            if attempt == 1:
                # 任务启动随机间隔, 错峰减轻 429 (重试等待单独由退避控制)
                time.sleep(random.uniform(*START_JITTER))
            log(f"{label} 开始下载 (第 {attempt}/{RETRY_MAX + 1} 次): {short}")
            with Heartbeat(f"{label} {short}",
                           detail=lambda: f"临时文件 {temp_size_str(output_dir)}"):
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            log(f"[OK]    {label} {short}")
            return {'status': 'ok', 'name': name, 'error': ''}
        except yt_dlp.utils.DownloadError as e:
            if is_rate_limit_error(e) and attempt <= RETRY_MAX:
                wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
                log(f"[WARN]  {label} {short}: 触发 429 限流, {wait}s 后重试 "
                    f"({attempt}/{RETRY_MAX})")
                time.sleep(wait)
                continue
            s = str(e)
            # 兜底: 归档预检查没覆盖到的"已存在"仍按跳过处理
            if 'already' in s and ('recorded' in s or 'downloaded' in s):
                log(f"[SKIP]  {label} {short} (归档中已存在)")
                return {'status': 'skip', 'name': name, 'error': ''}
            log(f"[FAIL]  {label} {short}: {clip(s)}")
            return {'status': 'fail', 'name': name, 'error': s}
        except Exception as e:
            log(f"[FAIL]  {label} {short}: {e}")
            return {'status': 'fail', 'name': name, 'error': str(e)}

    return {'status': 'fail', 'name': name, 'error': 'retries exhausted'}


def archive_path_for(args) -> str:
    """按模式/画质使用独立归档: 音频与不同画质互不干扰 (yt-dlp 归档不记录格式)."""
    if args.audio_only:
        name = 'download_archive_audio.txt'
    elif args.quality and args.quality > 0:
        name = f'download_archive_{args.quality}.txt'
    else:
        name = 'download_archive_best.txt'
    return os.path.join(args.output_dir, name)


def main():
    parser = argparse.ArgumentParser(
        description="多线程 YouTube 视频下载器 (yt-dlp + aria2c)")
    parser.add_argument('urls', nargs='*', help='视频或播放列表 URL (可多个)')
    parser.add_argument('-f', '--url-file', help='URL 列表文件, 每行一个')
    parser.add_argument('-o', '--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help=f'输出目录 (默认 {DEFAULT_OUTPUT_DIR})')
    parser.add_argument('-w', '--workers', type=int, default=DEFAULT_WORKERS,
                        help=f'并发线程数 (默认 {DEFAULT_WORKERS}, '
                             f'过高易触发 429 限流)')
    parser.add_argument('-q', '--quality', type=int, default=DEFAULT_QUALITY,
                        help='最大视频高度, 0=最佳 (默认 1080)')
    parser.add_argument('-a', '--audio-only', action='store_true',
                        help='仅提取音频 (mp3)')
    parser.add_argument('--aria2c', default='',
                        help=f'aria2c 路径 (默认探测: {ARIA2C_DEFAULT_PATH})')
    parser.add_argument('--cookies', default='',
                        help='Netscape 格式 cookies 文件')
    parser.add_argument('--cookies-from-browser', default='',
                        help='从浏览器取 cookies, 如 chrome / firefox / edge')
    args = parser.parse_args()

    t_all = time.time()

    # ---- [1/4] 收集输入 / 初始化 ----
    log("[info] [1/4] 初始化: 收集输入 / 探测 aria2c / 准备输出目录")
    urls = list(args.urls)
    if args.url_file:
        with open(args.url_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    urls.append(line)
    if not urls:
        parser.print_usage()
        print('\n[error] 请提供至少一个 URL 或 -f 列表文件')
        return 1

    # 探测 aria2c
    aria2c = find_aria2c(args.aria2c)
    if aria2c and probe_aria2c(aria2c):
        log(f"[info]   └ aria2c: {aria2c} ({ARIA2C_SPLIT}连接分块下载)")
    else:
        if aria2c:
            log(f"[warn]   └ aria2c 探测失败: {aria2c}")
        log("[warn]   └ 回退 yt-dlp 内置下载器 (单连接, 较慢)")
        aria2c = ''

    cookies_opts = parse_cookies_opts(args)
    if cookies_opts:
        log(f"[info]   └ cookies: {args.cookies or args.cookies_from_browser}")

    os.makedirs(args.output_dir, exist_ok=True)
    archive_path = archive_path_for(args)
    archive = ArchiveIndex(archive_path)
    log(f"[info]   └ 归档文件: {archive_path} (已有 {archive.count} 条记录)")
    log(f"[info] [1/4] 完成, 用时 {fmt_duration(time.time() - t_all)}")

    # ---- [2/4] 解析输入 (归档中已有的先跳过, 零请求) ----
    log(f"[info] [2/4] 解析输入: {len(urls)} 个 URL "
        f"(归档中已存在的会先跳过, 不联网解析)")
    t_stage = time.time()
    tasks = []
    skipped_urls = []
    for i, url in enumerate(urls, 1):
        tag = f"({i}/{len(urls)})"
        # 单个视频的 id 就在 URL 里 → 归档中已有的话连元数据请求都省掉
        vid = youtube_id_from_url(url)
        if vid and not is_playlist_url(url) and archive.is_archived(vid, 'Youtube', url):
            log(f"[SKIP]  {tag} {url} (归档中已存在, 未联网解析)")
            skipped_urls.append(url)
            continue
        log(f"[info] 解析 {tag}: {url}")
        t0 = time.time()
        try:
            with Heartbeat(f"解析 {tag} {url}"):
                items = expand_playlist_with_retry(url, cookies_opts, tag)
        except Exception as e:
            log(f"[FAIL]  无法解析 {url}: {clip(e)}")
            continue
        if len(items) > 1:
            log(f"[info]   └ 播放列表: {len(items)} 个视频 "
                f"(用时 {fmt_duration(time.time() - t0)})")
        else:
            log(f"[info]   └ 单个视频: {clip(items[0]['name'], 60)} "
                f"(用时 {fmt_duration(time.time() - t0)})")
        tasks.extend(items)

    if not tasks:
        if skipped_urls:
            log(f"[info] 全部 {len(skipped_urls)} 个 URL 都已在归档中, 无需下载 "
                f"(总用时 {fmt_duration(time.time() - t_all)})")
            return 0
        log('[info] 没有可下载的任务.')
        return 1
    log(f"[info] [2/4] 完成: 待检查 {len(tasks)} 个视频, "
        f"归档跳过 {len(skipped_urls)} 个 URL, "
        f"用时 {fmt_duration(time.time() - t_stage)}")

    # ---- [3/4] 归档预检查: 在线程池启动之前把"已下载"筛掉 ----
    log(f"[info] [3/4] 归档预检查 (线程池启动之前): {archive_path}")
    t_stage = time.time()
    pending, archived, unchecked = split_archived(tasks, archive)
    for task in archived:
        log(f"[SKIP]  {clip(task['name'], 60)} (归档中已存在)")
    log(f"[info] [3/4] 完成: 归档跳过合计 {len(skipped_urls) + len(archived)} 个 "
        f"(URL 阶段 {len(skipped_urls)} + 解析后 {len(archived)}), "
        f"待下载 {len(pending)} 个"
        f"{f', 无法预检查 {len(unchecked)} 个' if unchecked else ''}, "
        f"用时 {fmt_duration(time.time() - t_stage)}")
    if not pending:
        log(f"[info] 全部任务都已在归档中, 无需下载 "
            f"(总用时 {fmt_duration(time.time() - t_all)})")
        return 0

    # ---- [4/4] 并发下载 ----
    workers = max(1, args.workers)
    log(f"[info] [4/4] 并发下载: {len(pending)} 个任务, workers={workers}, "
        f"quality={'audio-only' if args.audio_only else args.quality or 'best'}, "
        f"output={os.path.abspath(args.output_dir)}")

    ydl_opts = build_ydl_opts(args, archive_path, aria2c)

    # 线程池并发下载 (每个任务独立 YoutubeDL 实例与进度回调, yt-dlp 非线程安全)
    results = [{'status': 'skip', 'name': u, 'error': ''} for u in skipped_urls]
    results.extend({'status': 'skip', 'name': t['name'], 'error': ''} for t in archived)
    total = len(pending)
    done = n_ok = n_skip = n_fail = 0
    t_stage = time.time()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(download_one, task, ydl_opts, i % workers + 1,
                            i + 1, total, args.output_dir): task
            for i, task in enumerate(pending)
        }
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            if r['status'] == 'ok':
                n_ok += 1
            elif r['status'] == 'skip':
                n_skip += 1
            else:
                n_fail += 1
            log(f"[info] 进度 {done}/{total}: 成功 {n_ok} / 跳过 {n_skip} / "
                f"失败 {n_fail} ← {clip(r['name'], 60)}")

    # 汇总
    ok = sum(1 for r in results if r['status'] == 'ok')
    skip = sum(1 for r in results if r['status'] == 'skip')
    fail = [r for r in results if r['status'] == 'fail']
    log(f"\n[info] 完成: {ok} 成功, {skip} 跳过, {len(fail)} 失败 "
        f"(下载用时 {fmt_duration(time.time() - t_stage)}, "
        f"总用时 {fmt_duration(time.time() - t_all)})")
    if fail:
        log("[info] 失败清单 (可修复网络后重跑, 已成功的自动跳过):")
        for r in fail:
            log(f"  - {clip(r['name'], 60)}: {clip(r['error'], 200)}")
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())

