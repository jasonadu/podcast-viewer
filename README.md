# subtitle-download

用 yt-dlp 下载视频字幕（默认英文），并输出为 SRT 格式。

## 使用

```bash
cd src/subtitle_download
python -m subtitle_download.main
```

或在代码中：

```python
from main import download_subtitle_lib
download_subtitle_lib("https://www.youtube.com/watch?v=xxxx", sub_langs=["en"])
```

字幕输出到 `./subtitles` 目录。

## SE Radio 节目浏览器

构建单页浏览/试听页面（分类、搜索、播放器）：

```bash
python tools/build_viewer.py
```

字幕**不内嵌**进 HTML，而是在点开播放器时从 `subtitles/` 目录动态加载、在浏览器端解析。
构建输出两个文件（节目数据与字幕均**不内嵌**，页面打开时动态加载）：
- `dist/se_radio_viewer.html` —— 页面骨架 + 分类配置（约 20 KB）
- `dist/se_radio_data.json` —— 全部节目数据（搜索/列表/详情）

由于浏览器禁止 `file://` 页面读取本地文件，需要通过本地 HTTP 服务打开：

```bash
cd d:/AI_Projects/subtitle_download
python -m http.server 8000
# 浏览器访问 http://localhost:8000/dist/se_radio_viewer.html
```

新增/更新字幕后重新运行 `build_viewer.py` 即可（构建只刷新"哪些集有字幕"的索引，速度很快）。

## 说明

- `convertsubtitles: 'srt'` 选项依赖 **ffmpeg**。如果系统已安装 ffmpeg，yt-dlp 会自动把字幕转成 `.srt`（推荐安装：`winget install Gyan.FFmpeg`）。
- 如果没有 ffmpeg，yt-dlp 会跳过转换，此时脚本内置的纯 Python 兜底逻辑会把残留的 `.vtt`（WebVTT）转成 `.srt`，并自动清理 YouTube 自动字幕的滚动重复行。
- 访问 YouTube 需要网络可达（必要时配置代理环境变量 `HTTP_PROXY` / `HTTPS_PROXY`）。
- YouTube 限流：批量下载遇到 `HTTP Error 429: Too Many Requests` 是 YouTube 对本机 IP 的速率限制。缓解办法：降低并发（`main.py` 中 `DEFAULT_WORKERS = 1`）、增大下载后的随机等待（`WAIT_MIN`/`WAIT_MAX`）、走代理/VPN，或给 yt-dlp 配置浏览器 cookies；被限流后隔一段时间重跑即可（已成功的 srt 会自动跳过）。

## substack的播客Pramatic Engineer

[下载substack的播客Pramatic Engineer](https://app.notion.com/p/substack-Pramatic-Engineer-3c78435ebe17808e8f08c6984884c4a6)

命令位于 src\subtitle_download\substack
```
pip install aria2p

python python -m subtitle_download.substack.download_podcasts

python substack_to_lrc_v3.py 458709_podcast_items.json

python substack_to_lrc_v3.py "https://newsletter.pragmaticengineer.com/p/dhhs-new-way-of-writing-code"
```