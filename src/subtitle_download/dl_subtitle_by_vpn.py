#!/usr/bin/env python3
"""
通过 SetupVPN 桥接代理下载字幕
==============================
参考 ``src/demo/test_setupvpn_proxy.py`` 的桥接方案：

  1. 从 Edge 浏覽器讀取 SetupVPN 擴件的遠端 HTTPS 代理位址 + 凭据
     （插件本身並無本地代理端口，凭据随会话輪換）；
  2. 启动一個本地 HTTP 代理橋接 (``http://127.0.0.1:8899``)::

         任意程序 -> 本地橋接 (plain HTTP, 無 TLS)
                    -> OpenSSL TLS + CONNECT + 自动注入凭据 -> SetupVPN 远端代理 -> 目标站

     橋接器用標準庫 ``ssl`` (OpenSSL 指纹) 连远程代理，从而繞過远程代理對
     BoringSSL 指纹 (如 curl_cffi) 的拒絕；
  3. 把本地橋接代理交給 ``download_subtitle_lib``，經代理訪問 YouTube 下載字幕。

注意（承襲 demo 文档的「兩層指纹检测」結論）：
  - 隧道內 (橋接 -> 目标站) 的 TLS 会被 MITM 拦截，Python 原生 ssl 指纹
    会被重置 (RST)，僅放行瀏覽器/curl 指纹。yt-dlp 缺省使用 Python 原生
    ssl，若访问失敗可參考 demo 裡的 curl_cffi 測試方案。

用法:
    python -m subtitle_download.dl_subtitle_by_vpn
    python src/subtitle_download/dl_subtitle_by_vpn.py [port]
"""
import atexit
import argparse
import importlib.util
import os
import sys
import threading
import time

# --------------------------------------------------------------------------- #
# 路徑預備：支援直接執行 (``python src/.../dl_subtitle_by_vpn.py``) 與
# 模块化執行 (``python -m subtitle_download.dl_subtitle_by_vpn``)。
# --------------------------------------------------------------------------- #
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)                       # src/
_DEMO_PATH = os.path.join(_SRC, "demo", "test_setupvpn_proxy.py")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# --------------------------------------------------------------------------- #
# 載入 demo 模块（demo 目录不是 package，改用文件路徑加載更穩妥）。
# demo 模块頂层僅匯入標準庫 + 定義函數/類，``main()`` 受 ``__name__`` 守卫
# 不會在導入時執行，因此導入安全且不依賴 curl_cffi。
# --------------------------------------------------------------------------- #
_spec = importlib.util.spec_from_file_location("test_setupvpn_proxy", _DEMO_PATH)
vpn_proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vpn_proxy)

SetupVPNBridge = vpn_proxy.SetupVPNBridge
edge_user_data_dir = vpn_proxy.edge_user_data_dir
get_proxy_server = vpn_proxy.get_proxy_server
get_credentials = vpn_proxy.get_credentials

from subtitle_download.main import download_subtitle_lib  # noqa: E402

# 本地橋接代理監聽端口 (可被命令列參數覆蓋)
DEFAULT_PORT = 8899
# 下載目標
VIDEO_URL = "https://www.youtube.com/watch?v=SHMCGrNJJGc"
SUB_LANGS = ["en"]


def start_vpn_bridge(port: int = DEFAULT_PORT):
    """提取 SetupVPN 配置並啟動本地橋接代理，返回橋接實例。"""
    user_data = edge_user_data_dir()
    host, p = get_proxy_server(user_data)
    login, token = get_credentials(user_data)

    print("=" * 62)
    print("SetupVPN 遠端代理（橋接信息）")
    print(f"  遠端代理 : {host}:{p}  (HTTPS 代理)")
    print(f"  代理帳號 : {login}")
    print(f"  代理令牌 : {token[:16]}...（隨會話輪換，橋接每次自動重取）")
    print("=" * 62)

    bridge = SetupVPNBridge(listen_port=port)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    time.sleep(0.5)  # 等待监听就緒
    return bridge


def main():
    # 命令列參數：本地橋接代理端口 + cookies 檔案
    parser = argparse.ArgumentParser(description='通過 SetupVPN 橋接代理下載字幕')
    parser.add_argument('port', nargs='?', type=int, default=DEFAULT_PORT,
                        help=f'本地橋接代理監聽端口 (默認 {DEFAULT_PORT})')
    parser.add_argument('--cookies', default='',
                        help='Netscape 格式 cookies 文件（年齡限制/會員視頻）')
    args = parser.parse_args()

    port = args.port
    cookies = args.cookies

    proxy_url = f"http://127.0.0.1:{port}"

    try:
        bridge = start_vpn_bridge(port)
    except Exception as e:
        print(f"[失敗] 啟動 SetupVPN 橋接失敗: {e}")
        print("      請先在 Edge 瀏覽器中連線 SetupVPN，再重新執行本脚本。")
        sys.exit(1)

    atexit.register(bridge.stop)

    print(f"[橋接] 本地代理已就緖: {proxy_url}")
    print(f"[下載] 經代理下載字幕: {VIDEO_URL}")

    # 經 SetupVPN 橋接下載字幕；download_subtitle_lib 會把 proxy 傳入 yt-dlp
    download_subtitle_lib(VIDEO_URL, sub_langs=SUB_LANGS, proxy=proxy_url, cookies=cookies)

    bridge.stop()
    print("[完成] 字幕下載完畢，橋接代理已關閉。")


if __name__ == "__main__":
    main()
