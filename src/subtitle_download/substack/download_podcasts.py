#!/usr/bin/env python3
"""
多线程播客下载器 (aria2c 分块下载 + 实时进度)
读取 JSON 数组 (含 mp3 / page 字段), 并发下载 mp3,
文件名为 page URL 最后一个 / 后的 slug + .mp3

下载引擎: 优先 aria2c (16连接分块, 速度稳定), 不可用时回退 urllib 单连接.
每个文件实时显示: 进度条 / 百分比 / 已下载/总大小 / 速度
底部显示总体统计和最近完成记录

用法:
  python3 download_podcasts.py <json_file> [输出目录] [并发数]
  python3 download_podcasts.py 458709_podcast_items.json ./downloads 8
  python3 download_podcasts.py 458709_podcast_items.json --aria2c "H:\\aria2\\aria2c.exe"
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait, FIRST_COMPLETED


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DEFAULT_WORKERS = 5
RETRY_TIMES = 3
RETRY_DELAY = 2  # 秒
CHUNK_SIZE = 65536
TIMEOUT = 120
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
REFRESH_INTERVAL = 0.3  # 进度刷新间隔(秒)
WRITE_THRESHOLD = 100 * 1024  # urllib 回退时每累积 100KB 写盘一次

# --- aria2c 配置 ---
ARIA2C_DEFAULT_PATH = r"D:\Tools\aria2-1.37.0-win-64bit-build1\aria2c.exe"
ARIA2C_SPLIT = 16          # 单文件分块数
ARIA2C_MAX_CONN = 16       # 单服务器最大连接数
ARIA2C_MIN_SPLIT = "1M"    # 最小分块大小
ARIA2C_TIMEOUT = 60         # 下载超时(秒)
ARIA2C_CONNECT_TIMEOUT = 30  # 连接超时(秒)
ARIA2C_MAX_TRIES = 5        # 最大重试次数
ARIA2C_RETRY_WAIT = 3       # 重试等待(秒)

# 全局停止事件 (Ctrl+C 时设置)
_g_stop = threading.Event()
# 全局 aria2c 路径 (main 中设置)
_g_aria2c = None


# ---------------------------------------------------------------------------
# Windows ANSI 支持
# ---------------------------------------------------------------------------
def _enable_windows_ansi() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
            return False
        return True
    except Exception:
        return False


_WIN_ANSI_ENABLED = _enable_windows_ansi()


def _supports_ansi() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        return _WIN_ANSI_ENABLED
    term = os.environ.get("TERM", "")
    if term in ("", "dumb", "unknown"):
        return False
    return True


# ---------------------------------------------------------------------------
# aria2c 工具
# ---------------------------------------------------------------------------
def _find_aria2c(custom_path: str = "") -> str:
    """查找 aria2c 可执行文件. 优先自定义路径, 其次默认路径, 最后 PATH."""
    candidates = []
    if custom_path:
        candidates.append(custom_path)
    candidates.append(ARIA2C_DEFAULT_PATH)
    # PATH 中的 aria2c / aria2c.exe
    for name in ("aria2c.exe", "aria2c"):
        candidates.append(name)

    for path in candidates:
        if os.path.isabs(path) and os.path.exists(path):
            return path
        if not os.path.isabs(path):
            # 尝试在 PATH 中查找
            from shutil import which
            found = which(path)
            if found:
                return found
    return ""


def _parse_aria2_size(num_str: str, unit: str) -> int:
    """把 aria2c 输出的大小 (如 '16MiB', '90M') 转为字节."""
    try:
        num = float(num_str)
    except ValueError:
        return 0
    u = unit.upper().strip()
    if u.startswith("GI") or u == "G":
        return int(num * 1024 ** 3)
    if u.startswith("MI") or u == "M":
        return int(num * 1024 ** 2)
    if u.startswith("KI") or u == "K":
        return int(num * 1024)
    return int(num)


# aria2c 进度行正则: 匹配 [#abcdef 16MiB/90MiB(17%) ...]
_ARIA2_PROGRESS_RE = re.compile(
    r"\[#\w+\s+([\d.]+)\s*([KMG]i?B?)\s*/\s*([\d.]+)\s*([KMG]i?B?)\s*\((\d+)%\)"
)


def download_with_aria2(url: str, filepath: str, tracker, filename: str) -> dict:
    """用 aria2c 下载单个文件, 解析输出获取实时进度.
    返回 {"status": "ok"/"fail", "error": str}"""
    aria2c = _g_aria2c
    if not aria2c:
        return {"status": "fail", "error": "aria2c 不可用"}

    out_dir = os.path.dirname(filepath) or "."
    out_name = os.path.basename(filepath)

    # 构建命令
    cmd = [
        aria2c,
        f"--dir={out_dir}",
        f"--out={out_name}",
        f"--split={ARIA2C_SPLIT}",
        f"--max-connection-per-server={ARIA2C_MAX_CONN}",
        f"--min-split-size={ARIA2C_MIN_SPLIT}",
        "--continue=true",
        f"--max-tries={ARIA2C_MAX_TRIES}",
        f"--retry-wait={ARIA2C_RETRY_WAIT}",
        f"--timeout={ARIA2C_TIMEOUT}",
        f"--connect-timeout={ARIA2C_CONNECT_TIMEOUT}",
        "--summary-interval=1",
        "--show-console-readout=false",
        "--console-log-level=warn",
        "--file-allocation=none",
        "--no-conf=true",
        f"--user-agent={UA}",
        url,
    ]

    # 用一个事件通知进度解析线程停止
    parse_stop = threading.Event()
    proc = None

    def _parse_output(stdout):
        """后台线程: 逐行读取 aria2c 输出, 正则解析进度并更新 tracker."""
        total_known = 0
        for line in stdout:
            if parse_stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            m = _ARIA2_PROGRESS_RE.search(line)
            if m:
                downloaded = _parse_aria2_size(m.group(1), m.group(2))
                total = _parse_aria2_size(m.group(3), m.group(4))
                if total > 0:
                    total_known = total
                if total_known > 0 and downloaded > 0:
                    tracker.update(filename, downloaded)
                elif total == 0 and total_known > 0:
                    tracker.update(filename, downloaded)
        return total_known

    try:
        # 启动子进程, stdout=PIPE 用于解析进度, stderr 合并到 stdout
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        # 先注册 tracker (总大小未知, 先设 0, 解析到后 update)
        tracker.start(filename, 0)

        # 启动进度解析线程
        parse_thread = threading.Thread(target=_parse_output, args=(proc.stdout,), daemon=True)
        parse_thread.start()

        # 轮询等待进程结束, 同时检测 Ctrl+C
        while proc.poll() is None:
            if _g_stop.is_set():
                # 用户中断: 终止 aria2c 进程
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                parse_stop.set()
                # 清理不完整文件和 .aria2 控制文件
                for p in (filepath, filepath + ".aria2"):
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                tracker.finish(filename, "fail", "已中断")
                return {"status": "fail", "error": "用户中断"}
            time.sleep(0.2)

        # 进程结束, 等待解析线程收尾
        parse_stop.set()
        parse_thread.join(timeout=2)

        retcode = proc.returncode
        if retcode == 0 and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            # 下载成功, 更新最终进度
            final_size = os.path.getsize(filepath)
            tracker.update(filename, final_size)
            tracker.finish(filename, "ok")
            return {"status": "ok", "error": ""}
        else:
            # 读取最后几行输出作为错误信息
            err = f"aria2c 退出码 {retcode}"
            tracker.finish(filename, "fail", err[:40])
            return {"status": "fail", "error": err}

    except FileNotFoundError:
        return {"status": "fail", "error": f"找不到 aria2c: {aria2c}"}
    except Exception as e:
        if proc and proc.poll() is None:
            proc.kill()
        parse_stop.set()
        tracker.finish(filename, "fail", str(e)[:40])
        return {"status": "fail", "error": str(e)}


# ---------------------------------------------------------------------------
# 实时进度追踪器
# ---------------------------------------------------------------------------
class ProgressTracker:
    def __init__(self, total: int, ansi_mode: bool):
        self._lock = threading.Lock()
        self._total = total
        self._ansi = ansi_mode
        self._active = {}
        self._ok = 0
        self._skip = 0
        self._fail = 0
        self._done = 0
        self._finished = []
        self._max_finished = 5
        self._last_lines = 0
        self._last_simple_time = 0.0
        self._SIMPLE_INTERVAL = 2.0

    def start(self, name: str, total: int):
        with self._lock:
            self._active[name] = {"downloaded": 0, "total": total, "start": time.time()}

    def update(self, name: str, downloaded: int):
        with self._lock:
            if name in self._active:
                self._active[name]["downloaded"] = downloaded
                # 如果总大小之前未知(0), 但 downloaded > 0, 保持 total=0 直到知道
                # aria2c 解析到总大小后会通过 update 传入, 这里不更新 total

    def set_total(self, name: str, total: int):
        """设置文件总大小 (aria2c 解析到后调用)."""
        with self._lock:
            if name in self._active:
                self._active[name]["total"] = total

    def finish(self, name: str, status: str, detail: str = ""):
        with self._lock:
            self._active.pop(name, None)
            self._done += 1
            if status == "ok":
                self._ok += 1
            elif status == "skip":
                self._skip += 1
            else:
                self._fail += 1
            self._finished.append((name, status, detail))
            if len(self._finished) > self._max_finished:
                self._finished.pop(0)

    def _fmt_size(self, n: int) -> str:
        if n >= 1024 * 1024 * 1024:
            return f"{n / 1024 / 1024 / 1024:.2f}GB"
        if n >= 1024 * 1024:
            return f"{n / 1024 / 1024:.1f}MB"
        return f"{n / 1024:.0f}KB"

    def render(self) -> str:
        with self._lock:
            lines = []
            pct = self._done / self._total * 100 if self._total else 0
            lines.append(
                f"总体进度: {self._done}/{self._total} ({pct:5.1f}%)  "
                f"成功={self._ok}  跳过={self._skip}  失败={self._fail}"
            )
            lines.append("-" * 72)
            if self._active:
                active_sorted = sorted(self._active.items(), key=lambda x: x[1]["start"])
                for name, info in active_sorted:
                    downloaded = info["downloaded"]
                    total = info["total"]
                    elapsed = max(time.time() - info["start"], 0.001)
                    speed = downloaded / elapsed
                    if total > 0:
                        bar_len = 28
                        filled = int(bar_len * downloaded / total)
                        bar = "#" * filled + "-" * (bar_len - filled)
                        lines.append(
                            f"  {name[:38]:38s} [{bar}] "
                            f"{downloaded / total * 100:5.1f}%  "
                            f"{self._fmt_size(downloaded)}/{self._fmt_size(total)}  "
                            f"{self._fmt_size(int(speed))}/s"
                        )
                    else:
                        lines.append(
                            f"  {name[:38]:38s} [{'?':>28s}]  "
                            f"{self._fmt_size(downloaded)}  "
                            f"{self._fmt_size(int(speed))}/s"
                        )
            else:
                lines.append("  (无活跃下载)")
            if self._finished:
                lines.append("-" * 72)
                lines.append("最近完成:")
                for name, status, detail in self._finished:
                    icon = {"ok": "[OK]", "skip": "[SKIP]", "fail": "[FAIL]"}.get(status, "[?]")
                    line = f"  {icon} {name[:50]}"
                    if detail:
                        line += f"  ({detail})"
                    lines.append(line)
            return "\n".join(lines)

    def render_summary(self) -> str:
        with self._lock:
            pct = self._done / self._total * 100 if self._total else 0
            active = len(self._active)
            return (
                f"进度 {self._done}/{self._total} ({pct:5.1f}%) "
                f"OK={self._ok} SKIP={self._skip} FAIL={self._fail} ACTIVE={active}"
            )

    def paint(self):
        if self._ansi:
            text = self.render()
            lines = text.count("\n") + 1
            if self._last_lines > 0:
                sys.stdout.write(f"\033[{self._last_lines}A")
            sys.stdout.write("\033[J" + text)
            sys.stdout.flush()
            self._last_lines = lines
        else:
            now = time.time()
            if now - self._last_simple_time >= self._SIMPLE_INTERVAL:
                self._last_simple_time = now
                sys.stdout.write(self.render_summary() + "\n")
                sys.stdout.flush()

    def final_paint(self):
        if self._ansi:
            if self._last_lines > 0:
                sys.stdout.write(f"\033[{self._last_lines}A\033[J")
            sys.stdout.write(self.render() + "\n")
        else:
            sys.stdout.write(self.render() + "\n")
        sys.stdout.flush()
        self._last_lines = 0


def _monitor_loop(tracker: ProgressTracker, stop_event: threading.Event):
    while not stop_event.is_set():
        tracker.paint()
        time.sleep(REFRESH_INTERVAL)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _slug_from_page(page: str) -> str:
    path = urllib.parse.urlparse(page).path
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    slug = "".join(c for c in slug if c not in '\\/:*?"<>|')
    return slug or "untitled"


def _safe_name(filepath: str) -> str:
    if not os.path.exists(filepath):
        return filepath
    base, ext = os.path.splitext(filepath)
    i = 1
    while os.path.exists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


# ---------------------------------------------------------------------------
# urllib 回退下载 (aria2c 不可用时使用)
# ---------------------------------------------------------------------------
def _download_with_urllib(url: str, filepath: str, tracker, filename: str) -> dict:
    tmp_path = filepath + ".part"
    for attempt in range(1, RETRY_TIMES + 1):
        if _g_stop.is_set():
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                tracker.start(filename, total)
                downloaded = 0
                buf = b""
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        buf += chunk
                        downloaded += len(chunk)
                        tracker.update(filename, downloaded)
                        if _g_stop.is_set():
                            if buf:
                                f.write(buf)
                            break
                        if len(buf) >= WRITE_THRESHOLD:
                            f.write(buf)
                            buf = b""
                    if buf:
                        f.write(buf)
            if _g_stop.is_set():
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                tracker.finish(filename, "fail", "已中断")
                return {"status": "fail", "error": "用户中断"}
            if os.path.exists(filepath):
                filepath = _safe_name(filepath)
            os.rename(tmp_path, filepath)
            tracker.finish(filename, "ok")
            return {"status": "ok", "error": ""}
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if _g_stop.is_set():
                tracker.finish(filename, "fail", "已中断")
                return {"status": "fail", "error": "用户中断"}
            if attempt < RETRY_TIMES:
                tracker.update(filename, 0)
                time.sleep(RETRY_DELAY * attempt)
            else:
                tracker.finish(filename, "fail", str(e)[:40])
                return {"status": "fail", "error": str(e)}
    tracker.finish(filename, "fail", "已中断" if _g_stop.is_set() else "未知错误")
    return {"status": "fail", "error": "用户中断" if _g_stop.is_set() else "未知错误"}


# ---------------------------------------------------------------------------
# 下载入口: 优先 aria2c, 回退 urllib
# ---------------------------------------------------------------------------
def download_one(item: dict, out_dir: str, tracker: ProgressTracker, force: bool = False) -> dict:
    mp3_url = item.get("mp3", "").strip()
    page = item.get("page", "").strip()
    if not mp3_url:
        return {"status": "fail", "name": "", "url": mp3_url, "error": "mp3 url 为空"}

    slug = _slug_from_page(page)
    filename = f"{slug}.mp3"
    filepath = os.path.join(out_dir, filename)

    # 已存在且非空 -> 跳过
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0 and not force:
        tracker.finish(filename, "skip", "已存在")
        return {"status": "skip", "name": filename, "url": mp3_url, "error": ""}

    # 强制覆盖: 先删旧文件和 .aria2 控制文件
    if force:
        for p in (filepath, filepath + ".aria2"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # 优先 aria2c
    if _g_aria2c:
        result = download_with_aria2(mp3_url, filepath, tracker, filename)
        if result["status"] == "ok":
            return {"status": "ok", "name": filename, "url": mp3_url, "error": ""}
        if _g_stop.is_set():
            return {"status": "fail", "name": filename, "url": mp3_url, "error": result.get("error", "用户中断")}
        # aria2c 失败, 回退 urllib (清理可能的残留)
        for p in (filepath, filepath + ".aria2", filepath + ".part"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # 回退 urllib 单连接
    result = _download_with_urllib(mp3_url, filepath, tracker, filename)
    result["name"] = filename
    result["url"] = mp3_url
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    global _g_aria2c

    parser = argparse.ArgumentParser(description="多线程播客下载器 (aria2c分块 + 实时进度)")
    parser.add_argument("json_file", help="JSON 文件路径 (含 mp3 / page 字段的数组)")
    parser.add_argument("output_dir", nargs="?", default="./podcasts", help="输出目录 (默认 ./podcasts)")
    parser.add_argument("workers", nargs="?", type=int, default=DEFAULT_WORKERS, help=f"并发文件数 (默认 {DEFAULT_WORKERS})")
    parser.add_argument("--aria2c", default="", help=f"aria2c 可执行文件路径 (默认 {ARIA2C_DEFAULT_PATH})")
    parser.add_argument("--no-aria2", action="store_true", help="禁用 aria2c, 只用 urllib 单连接下载")
    parser.add_argument("--force", action="store_true", help="强制覆盖已存在的文件")
    parser.add_argument("--no-progress", action="store_true", help="禁用实时进度")
    args = parser.parse_args()

    # 查找 aria2c
    if args.no_aria2:
        _g_aria2c = ""
        print("[info] 已禁用 aria2c, 使用 urllib 单连接下载")
    else:
        _g_aria2c = _find_aria2c(args.aria2c)
        if _g_aria2c:
            print(f"[info] 使用 aria2c: {_g_aria2c} (16连接分块下载)")
        else:
            print("[warn] 未找到 aria2c, 回退 urllib 单连接下载 (速度较慢)")

    # 读取 JSON
    if not os.path.exists(args.json_file):
        print(f"[error] 文件不存在: {args.json_file}", file=sys.stderr)
        sys.exit(1)
    with open(args.json_file, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        print("[error] JSON 顶层不是数组", file=sys.stderr)
        sys.exit(1)
    items = [it for it in items if it.get("mp3")]
    if not items:
        print("[error] 没有可下载的条目", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    ansi_mode = _supports_ansi() and not args.no_progress
    show_progress = not args.no_progress
    tracker = ProgressTracker(total=len(items), ansi_mode=ansi_mode)

    print(f"[info] 共 {len(items)} 条, 并发 {args.workers}, 输出目录: {args.output_dir}")
    if args.force:
        print("[info] 强制覆盖模式")
    if show_progress and not ansi_mode:
        print("[info] 终端不支持 ANSI, 使用定时摘要输出进度 (每2秒一行)")
    print()

    # 启动监控线程
    stop_event = threading.Event()
    monitor_thread = None
    if show_progress:
        monitor_thread = threading.Thread(target=_monitor_loop, args=(tracker, stop_event), daemon=True)
        monitor_thread.start()

    # 重置全局停止事件
    _g_stop.clear()

    # 注册 SIGINT
    def _on_sigint(signum, frame):
        _g_stop.set()
    signal.signal(signal.SIGINT, _on_sigint)

    # 多线程下载 (轮询, 不阻塞)
    failures = []
    executor = None
    try:
        executor = ThreadPoolExecutor(max_workers=args.workers)
        futures = [executor.submit(download_one, item, args.output_dir, tracker, args.force) for item in items]
        pending = set(futures)
        while pending:
            if _g_stop.is_set():
                break
            done, pending = futures_wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    result = f.result()
                    if result["status"] == "fail":
                        failures.append(result)
                except Exception as e:
                    failures.append({"status": "fail", "name": "unknown", "url": "", "error": str(e)})
    finally:
        stop_event.set()
        if monitor_thread:
            monitor_thread.join(timeout=1)

    # Ctrl+C 中断处理
    if _g_stop.is_set():
        if executor:
            for f in futures:
                f.cancel()
            executor.shutdown(wait=False)
        # 清理 .part / .aria2 残留
        cleaned = 0
        for fname in os.listdir(args.output_dir):
            if fname.endswith(".part") or fname.endswith(".aria2"):
                try:
                    os.remove(os.path.join(args.output_dir, fname))
                    cleaned += 1
                except OSError:
                    pass
        tracker.final_paint()
        print(f"\n[info] 已中断 (Ctrl+C), 清理了 {cleaned} 个临时文件")
        if failures:
            print(f"[warn] {len(failures)} 条失败/中断:")
            for r in failures:
                print(f"  [FAIL] {r.get('name','?')}: {r.get('error','')}")
        os._exit(0)

    # 正常完成
    if executor:
        executor.shutdown(wait=True)

    tracker.final_paint()

    if failures:
        print(f"\n[warn] {len(failures)} 条下载失败:")
        for r in failures:
            print(f"  [FAIL] {r.get('name','?')}: {r.get('error','')}")
            if r.get("url"):
                print(f"    {r['url']}")


if __name__ == "__main__":
    main()
