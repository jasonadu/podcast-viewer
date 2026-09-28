#!/usr/bin/env python3
"""
多线程 YouTube 视频下载器 (yt-dlp + aria2c).

- 下载引擎: yt-dlp 解析/元数据 + aria2c 多连接分块传输 (-x16 -s16 -k1M),
  音视频合并依赖 ffmpeg (PATH 中已安装).
- 支持三种输入: 单个/多个命令行 URL、URL 列表文件 (-f, 每行一个)、播放列表 URL.
- 并发策略 (简单模式): 默认 workers=2, 需要提速时手动 --workers 5.
  注意: 429 限流主要发生在元数据提取阶段 (访问 youtube.com),
  提高 workers 会更快触发限流; 配合 download-archive 可中断后重跑自动跳过.

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

# 线程安全的输出锁
_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


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


def expand_playlist(url: str, cookies_opts: dict) -> list:
    """播放列表 URL → 视频条目列表 [(视频url, 标题)]. 其他 URL 返回单元素列表."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',   # 只取条目, 不解析每个视频的详细流
        'skip_download': True,
        **cookies_opts,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get('_type') not in ('playlist', 'multi_video'):
        return [(url, info.get('title') or url)]

    entries = info.get('entries') or []
    items = []
    for e in entries:
        # extract_flat 下 id 就是视频 id, 拼 watch URL 最稳妥
        vid = e.get('url') or e.get('id') or ''
        if e.get('ie_key') == 'Youtube' and vid and not vid.startswith('http'):
            vid = f"https://www.youtube.com/watch?v={vid}"
        if not vid:
            continue
        items.append((vid, e.get('title') or vid))
    return items


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
        'no_warnings': True,
        'retries': 3,
        'fragment_retries': 3,
        'noplaylist': True,   # 每个任务只下载单个视频 (播放列表已提前展开)
        'download_archive': archive_path,   # 归档, 中断后重跑自动跳过
        'progress_hooks': [make_progress_hook()],
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


def make_progress_hook():
    """进度回调: 打印下载进度行 (线程安全)."""
    state = {'last': ''}

    def hook(d):
        if d.get('status') == 'finished':
            log("      └ 分片下载完成, 后处理中...")
            return
        if d.get('status') != 'downloading':
            return
        try:
            pct = str(d.get('_percent_str', '?')).strip()
            speed = str(d.get('_speed_str', '?')).strip()
            eta = str(d.get('_eta_str', '?')).strip()
            line = f"      {pct:>7} {speed:>12} ETA {eta:<8}"
            if line != state['last']:
                state['last'] = line
                with _print_lock:
                    sys.stdout.write("\r" + line.ljust(100))
                    sys.stdout.flush()
        except Exception:
            pass

    return hook


def is_rate_limit_error(err: Exception) -> bool:
    s = str(err)
    return '429' in s or 'Too Many Requests' in s


def download_one(task: dict, ydl_opts: dict, worker_idx: int) -> dict:
    """下载单个视频, 带 429 指数退避重试.

    返回 {'status': 'ok'|'skip'|'fail', 'name': str, 'error': str}
    """
    url = task['url']
    name = task['name']

    # 任务启动随机间隔, 错峰减轻 429
    time.sleep(random.uniform(*START_JITTER))

    for attempt in range(1, RETRY_MAX + 2):
        try:
            log(f"[worker-{worker_idx}] 下载中: {name}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            with _print_lock:
                sys.stdout.write("\n")
            log(f"[OK]    {name}")
            return {'status': 'ok', 'name': name, 'error': ''}
        except yt_dlp.utils.DownloadError as e:
            if is_rate_limit_error(e) and attempt <= RETRY_MAX:
                wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
                log(f"[WARN]  {name}: 触发 429 限流, {wait}s 后重试 "
                    f"({attempt}/{RETRY_MAX})")
                time.sleep(wait)
                continue
            # 归档中已存在等情况视为跳过
            s = str(e)
            if 'already' in s and ('recorded' in s or 'downloaded' in s):
                log(f"[SKIP]  {name} (已下载)")
                return {'status': 'skip', 'name': name, 'error': ''}
            log(f"[FAIL]  {name}: {e}")
            return {'status': 'fail', 'name': name, 'error': s}
        except Exception as e:
            log(f"[FAIL]  {name}: {e}")
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

    # 收集 URL
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
        log(f"[info] 使用 aria2c: {aria2c} ({ARIA2C_SPLIT}连接分块下载)")
    else:
        if aria2c:
            log(f"[warn] aria2c 探测失败: {aria2c}")
        log("[warn] 回退 yt-dlp 内置下载器 (单连接, 较慢)")
        aria2c = ''

    cookies_opts = parse_cookies_opts(args)
    if cookies_opts:
        log(f"[info] 已配置 cookies: {args.cookies or args.cookies_from_browser}")

    os.makedirs(args.output_dir, exist_ok=True)
    archive_path = archive_path_for(args)
    log(f"[info] 归档文件: {archive_path}")

    # 展开播放列表 / 逐个提取标题
    tasks = []
    for url in urls:
        try:
            items = expand_playlist(url, cookies_opts)
            if len(items) > 1:
                log(f"[info] 播放列表: {len(items)} 个视频 ← {url}")
        except Exception as e:
            log(f"[FAIL]  无法解析 {url}: {e}")
            continue
        for vid, title in items:
            tasks.append({'url': vid, 'name': title})

    if not tasks:
        print('[info] 没有可下载的任务.')
        return 1

    log(f"[info] 待下载: {len(tasks)} 个, workers={args.workers}, "
        f"quality={'audio-only' if args.audio_only else args.quality or 'best'}, "
        f"output={os.path.abspath(args.output_dir)}")

    ydl_opts = build_ydl_opts(args, archive_path, aria2c)

    # 线程池并发下载 (每个任务独立 YoutubeDL 实例, yt-dlp 非线程安全)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(download_one, task, ydl_opts,
                            i % max(1, args.workers) + 1): task
            for i, task in enumerate(tasks)
        }
        for fut in as_completed(futures):
            results.append(fut.result())

    # 汇总
    ok = sum(1 for r in results if r['status'] == 'ok')
    skip = sum(1 for r in results if r['status'] == 'skip')
    fail = [r for r in results if r['status'] == 'fail']
    log(f"\n[info] 完成: {ok} 成功, {skip} 跳过, {len(fail)} 失败")
    if fail:
        log("[info] 失败清单 (可修复网络后重跑, 已成功的自动跳过):")
        for r in fail:
            log(f"  - {r['name']}: {r['error'][:200]}")
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())

