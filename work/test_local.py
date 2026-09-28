#!/usr/bin/env python3
"""本地端到端测试 download_video.py:
1) 起 ThreadingHTTPServer 提供测试文件 (不受外网/YouTube 可达性影响)
2) 多视频并发下载 (workers=2)  -> 两个文件都成功
3) 重跑同一 URL               -> download-archive 归档预检查直接跳过
4) --audio-only 单文件        -> ffmpeg 提取 mp3
5) URL 自带 video id 且已归档  -> 零请求秒退 (连元数据都不解析)
全程经 yt-dlp generic 提取器, 与 YouTube 路径共用 aria2c 外部下载器逻辑.
测试媒体由 ffmpeg 现场生成 (mpeg4 + aac), 是"真能解码"的文件,
否则 ffprobe 解不开, 第 4 步必然假失败.
子进程 stdout 直接透传到本进程 stdout (实时可见, 便于诊断).
"""
import http.server
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'aria2_test_src')
OUT = os.path.join(ROOT, 'aria2_test_out')
PORT = 18766
PY = sys.executable


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """静默日志 + 吞掉 aria2c 并发连接的断开异常. 服务目录固定为 SRC."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=SRC, **kw)

    def log_message(self, *a):
        pass

    def copyfile(self, src, dst):
        try:
            return super().copyfile(src, dst)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True


def make_sample(path: str, seconds: int = 20) -> bool:
    """用 ffmpeg 生成真实可解码的测试媒体 (mpeg4 视频 + aac 音频)."""
    cmd = ['ffmpeg', '-y',
           '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
           '-f', 'lavfi', '-i', f'testsrc=size=640x360:rate=15:duration={seconds}',
           '-c:v', 'mpeg4', '-q:v', '5', '-c:a', 'aac', '-shortest', path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(path):
        print(f"[test] ffmpeg 生成测试媒体失败: {r.stderr[-300:]}", flush=True)
        return False
    return True


def run_download(args: list) -> int:
    cmd = [PY, '-m', 'subtitle_download.download_video'] + args
    print(f"\n===== 运行: {' '.join(args)}", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, timeout=120)   # stdout 直接继承, 实时输出
        print(f"----- 返回码: {r.returncode} 用时 {time.time()-t0:.1f}s",
              flush=True)
        return r.returncode
    except subprocess.TimeoutExpired:
        print(f"----- 超时 120s, 用时 {time.time()-t0:.1f}s", flush=True)
        return 99


def main():
    # 准备测试媒体: ffmpeg 现场生成真实可解码的 mp4 (mpeg4 视频 + aac 音频)
    os.makedirs(SRC, exist_ok=True)
    for name in ('sample_a.mp4', 'sample_b.mp4'):
        p = os.path.join(SRC, name)
        if not os.path.exists(p) and not make_sample(p):
            print('[test] 无法生成测试媒体, 退出', flush=True)
            return 2
    # 干净的输出目录
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    # 本地 HTTP 服务
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), QuietHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[test] 本地服务已启动: http://127.0.0.1:{PORT}/", flush=True)

    base = f"http://127.0.0.1:{PORT}"
    ok = True

    # 测试1: 多视频并发 (2 URL, workers=2)
    rc = run_download([f"{base}/sample_a.mp4", f"{base}/sample_b.mp4",
                       '-o', OUT, '-w', '2'])
    ok &= (rc == 0)
    files = os.listdir(OUT)
    n_mp4 = len([f for f in files if f.endswith('.mp4')])
    print(f"\n[test1] 输出文件: {files} (mp4 x{n_mp4}, 期望 2)", flush=True)
    ok &= n_mp4 == 2

    # 测试2: 重跑 sample_a -> 归档跳过 (秒级完成)
    t0 = time.time()
    rc = run_download([f"{base}/sample_a.mp4", '-o', OUT])
    dt = time.time() - t0
    print(f"[test2] 重跑返回码: {rc}, 用时 {dt:.1f}s (期望 0 且 <20s, "
          f"归档跳过不重复下载)", flush=True)
    ok &= (rc == 0 and dt < 20)

    # 测试3: audio-only 提取 mp3
    rc = run_download([f"{base}/sample_a.mp4", '-o', OUT, '-a'])
    files = os.listdir(OUT)
    n_mp3 = len([f for f in files if f.endswith('.mp3')])
    print(f"[test3] 输出文件: {files} (mp3 x{n_mp3}, 期望 1)", flush=True)
    ok &= n_mp3 == 1

    # 测试4: URL 自带 video id 且归档中已有 -> 零请求跳过 (不需要联网)
    with open(os.path.join(OUT, 'download_archive_1080.txt'), 'a',
              encoding='utf-8') as f:
        f.write('youtube 2Y1Z6u_5Arw\n')
    t0 = time.time()
    rc = run_download(['https://youtu.be/2Y1Z6u_5Arw', '-o', OUT])
    dt = time.time() - t0
    print(f"[test4] YouTube URL 归档跳过: rc={rc}, 用时 {dt:.1f}s "
          f"(期望 0 且 <10s: 不联网、不解析、不进线程池)", flush=True)
    ok &= (rc == 0 and dt < 10)

    srv.shutdown()
    print(f"\n[test] 结果: {'全部通过' if ok else '存在失败'}", flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
