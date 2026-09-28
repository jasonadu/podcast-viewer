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
## YouTube 视频下载 (yt-dlp + aria2c)

```bash
uv run python -m subtitle_download.download_video <URL>                      # 单个
uv run python -m subtitle_download.download_video <URL1> <URL2>              # 多个
uv run python -m subtitle_download.download_video -f urls.txt -w 2           # 列表文件
uv run python -m subtitle_download.download_video "https://www.youtube.com/playlist?list=..."  # 播放列表
uv run python -m subtitle_download.download_video <URL> --audio-only         # 提取 mp3

python -m subtitle_download.download_video -f data\youtube-list.txt
python -m subtitle_download.download_video -f data\airflow_summit_2026.txt

python -m subtitle_download.download_video https://www.youtube.com/watch?list=PLzwJJv8h-iciW53inSOkQA4mkG8TuQAUh
```

- 引擎：yt-dlp 元数据 + aria2c 16 连接分块下载，ffmpeg 合并音视频（三者均已就位）。
- 默认 `--workers 2`（429 限流主要发生在元数据提取阶段，并发过高更快触发，见 issue #2）；需要提速再 `--workers 5`。
- 防护：任务启动随机间隔 3~8 秒、429 指数退避重试（8s→16s→32s）、`download_archive` 归档按画质/模式独立（中断后重跑自动跳过已成功的）。
- 归档预检查（两级，都在进入线程池之前）：① 单个视频的 id 直接从 URL 解析（不联网），归档中已有就立刻 `[SKIP] xxx (归档中已存在, 未联网解析)`；② 其余条目（播放列表展开后）解析完统一判定，同样打印 `[SKIP]`。已归档的都不占用 worker、不消耗下载请求；全部已下载时秒退（退出码 0）。
- 进度可见性：4 个阶段 `[1/4]~[4/4]` + 每个 URL/任务的编号进度（`[worker-1] (3/20)`）；解析、下载这类阻塞步骤超过 15s 会打印 `... 仍在进行, 已用 45s, 临时文件 12.3MiB` 心跳（aria2c 作为外部下载器只在结束时回调一次，没有增量进度，心跳是下载期间唯一的信息来源）；yt-dlp 的 warning/error 会带 `[worker-N]` 前缀转发出来，不再静默等待。
- 解析阶段（元数据/播放列表）遇到 429 也会自动退避重试（8s→16s→32s），不会因为一次限流就丢掉整个 URL。
- 输出：`./videos/<标题> [<id>].mp4`（`--output-dir` 可改）；年龄限制/会员视频需 `--cookies-from-browser chrome` 等。
- 访问 YouTube 需网络可达，必要时设置 `HTTP_PROXY` / `HTTPS_PROXY`（脚本自动透传）。

## substack的播客Pramatic Engineer

## substack的播客Pramatic Engineer

[下载substack的播客Pramatic Engineer](https://app.notion.com/p/substack-Pramatic-Engineer-3c78435ebe17808e8f08c6984884c4a6)

命令位于 src\subtitle_download\substack
```
pip install aria2p

python python -m subtitle_download.substack.download_podcasts

python substack_to_lrc_v3.py 458709_podcast_items.json

python substack_to_lrc_v3.py "https://newsletter.pragmaticengineer.com/p/dhhs-new-way-of-writing-code"
```