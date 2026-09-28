#!/usr/bin/env python3
"""验证"下载期间不再静默"的心跳机制 (download_video.py).

本地 HTTP 服务全局限速 100KiB/s, 让一次 3MiB 下载持续约 30s:
- 旧版: aria2c 作为外部下载器不上报增量进度 -> 全程没有任何输出, 看起来卡死
- 现在: 下载步骤的 Heartbeat 每 HEARTBEAT_INTERVAL(15s) 打印一行"仍在进行 + 已用时间"
断言: 子进程输出里出现 "下载中" 心跳行, 且返回码为 0.
"""
import http.server
import os
import socketserver
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'heartbeat_test_out')
PORT = 18767
SIZE = 3 * 1024 * 1024     # 3MiB
RATE = 100 * 1024          # 全局限速 100KiB/s -> 整文件约 30s
CHUNK = 16 * 1024          # 每次发送 16KiB


class RateLimiter:
    """全局令牌桶: 所有连接合计不超过 RATE 字节/秒, 保证下载足够慢."""

    def __init__(self, rate: int):
        self.rate = rate
        self.lock = threading.Lock()
        self.next_time = time.time()

    def acquire(self, n: int):
        with self.lock:
            now = time.time()
            self.next_time = max(self.next_time, now) + n / self.rate
            wait = self.next_time - now
        if wait > 0:
            time.sleep(wait)


LIMITER = RateLimiter(RATE)


class SlowHandler(http.server.BaseHTTPRequestHandler):
    """支持 Range 的限速文件服务 (aria2c 会分块并发请求, 这里每块都慢慢发)."""

    def log_message(self, *a):
        pass

    def handle_error(self, *a):
        pass    # 客户端取消连接属于正常现象, 不要打堆栈

    def do_GET(self):
        start, end = 0, SIZE - 1
        rng = self.headers.get('Range', '')
        if rng.startswith('bytes='):
            part = rng[len('bytes='):].split('-')
            try:
                if part[0]:
                    start = int(part[0])
                if len(part) > 1 and part[1]:
                    end = min(int(part[1]), SIZE - 1)
            except ValueError:
                start, end = 0, SIZE - 1

        length = max(0, end - start + 1)
        partial = bool(rng)
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(length))
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{SIZE}')
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()

        data = b'\0' * min(CHUNK, length)
        remaining = length
        try:
            while remaining > 0:
                piece = data[:remaining]
                LIMITER.acquire(len(piece))
                self.wfile.write(piece)
                self.wfile.flush()
                remaining -= len(piece)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        os.remove(os.path.join(OUT, name))

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), SlowHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{PORT}/slow.bin'
    print(f'[test] 限速服务: {url} ({SIZE // 1024}KiB @ {RATE // 1024}KiB/s, '
          f'约 {SIZE // RATE}s)', flush=True)

    cmd = [sys.executable, '-m', 'subtitle_download.download_video', url,
           '-o', OUT]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    dt = time.time() - t0
    out = proc.stdout + proc.stderr
    print(out, flush=True)
    srv.shutdown()

    heartbeats = [ln for ln in out.splitlines() if '仍在进行' in ln]
    ok = proc.returncode == 0 and len(heartbeats) >= 1
    print(f'[test] 返回码={proc.returncode} 用时={dt:.1f}s '
          f'心跳行数={len(heartbeats)}', flush=True)
    print(f"[test] 结果: {'通过' if ok else '失败'}", flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
