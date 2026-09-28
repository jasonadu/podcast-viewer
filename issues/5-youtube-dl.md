生成一个多线程下载YouTube视频的python脚本。要求如下
1. 使用 yt_dlp，aria2c
2. 支持单个，和多视频列表下载

---

# 环境已全部确认。下面是我的调研结论和完整方案。

## 环境检查结果（好消息：基本什么都不用装）

| 依赖 | 状态 | 位置 |
|---|---|---|
| yt-dlp | ✅ 已装（2026.08.19） | `.venv` 内 |
| aria2c | ✅ 已存在 | `D:\Tools\aria2-1.37.0-win-64bit-build1\aria2c.exe`（现有脚本同款路径） |
| ffmpeg | ✅ 已装（9.0.1，在 PATH 中） | `D:\Tools\ffmpeg-9.0.1-full_build\bin` |
| Python | 3.12.14，uv 管理项目 | — |

不需要新增任何 Python 依赖——不用 `aria2p`，直接通过 yt-dlp 的 `external_downloader` 机制调用 aria2c。

## 实施计划

**新建文件**：`d:\AI_Projects\subtitle_download\src\subtitle_download\download_video.py`（沿用项目现有风格：中文注释、argparse、`ThreadPoolExecutor`、`[info]/[warn]` 日志前缀）

**核心设计**：

1. **yt-dlp + aria2c 集成**
   ```python
   ydl_opts['external_downloader'] = {'default': 'aria2c'}
   ydl_opts['external_downloader_args'] = {'aria2c': ['-x','16','-s','16','-k','1M', ...]}
   ```
   aria2c 负责多连接分块下载，音视频流合并交给已有的 ffmpeg。

2. **多线程**：`ThreadPoolExecutor` 并发下载多个视频，每个任务独立创建 `YoutubeDL` 实例（yt-dlp 非线程安全，与现有 `main.py` 模式一致）。默认并发 **2**——YouTube 对高并发批量下载会返回 429 限流（README 里已记录过这个坑），并沿用 `WAIT_MIN/WAIT_MAX` 随机等待模式。

3. **三种输入方式**：
   - 命令行直接给 URL：单个或空格分隔的多个
   - `-f urls.txt`：文件列表（每行一个 URL）
   - 播放列表 URL：先用 `extract_flat` 拉取全部视频条目，再进线程池逐个下载

4. **可选功能**：`--workers`、`--quality`（默认 1080p）、`--audio-only`（提取 mp3）、`--output-dir`（默认 `./videos`）、`--aria2c`（自定义路径）、`--cookies` / `--cookies-from-browser`（年龄限制/会员视频）、已下载自动跳过（download-archive 归档）、失败汇总报告。

**使用示例**：
```bash
uv run python -m subtitle_download.download_video https://www.youtube.com/watch?v=xxx
uv run python -m subtitle_download.download_video url1 url2 url3
uv run python -m subtitle_download.download_video -f urls.txt --workers 3
uv run python -m subtitle_download.download_video "https://www.youtube.com/playlist?list=PLxxx"
```

**验证**：`--help` + aria2c 探测 → 用一个公开短视频实测单下载 → 实测列表下载和已存在跳过。

## 回答你的问题“还需要哪些东西”

**软件层面：什么都不缺**，aria2c / ffmpeg / yt-dlp 都已就位。真正需要你提供/确认的只有 3 点：

1. **网络可达 YouTube**——如果需要代理，运行前设置 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量即可，脚本会自动透传给 yt-dlp
2. **（可选）浏览器 cookies**——下载年龄限制或会员视频时才需要，脚本会提供 `--cookies-from-browser chrome` 选项
3. **确认默认参数**——画质 1080p、并发 2、输出目录 `./videos`，如果想改告诉我即可（脚本本身都支持命令行覆盖）

如果方案没问题，请 **toggle to Act mode**，我就开始写脚本并实际验证。

---

# 好，按**简单模式**定案。更新后的最终方案：

## 最终方案

**新建**：`d:\AI_Projects\subtitle_download\src\subtitle_download\download_video.py`

**并发策略（简单模式）**：
- 默认 `--workers 2`，需要提速时手动 `--workers 5`
- 保留三个廉价但有效的防护：任务启动随机间隔 3~8 秒、429 指数退避重试（8s→16s→32s）、`download-archive` 归档（中断后重跑自动跳过已成功的）

**核心实现**：
1. yt-dlp 通过 `external_downloader` 调 aria2c（`-x16 -s16 -k1M`，断点续传靠 `.aria2` 控制文件）
2. `ThreadPoolExecutor` 并发多视频，每个任务独立 `YoutubeDL` 实例（yt-dlp 非线程安全）
3. 三种输入：单个/多个命令行 URL、`-f urls.txt` 文件列表（每行一个）、播放列表 URL（`extract_flat` 先取条目再进线程池）
4. 其他参数：`--quality`（默认 1080p）、`--audio-only`（mp3 提取）、`--output-dir`（默认 `./videos`）、`--aria2c` 自定义路径、`--cookies` / `--cookies-from-browser`（年龄限制/会员视频）

**使用示例**：
```bash
uv run python -m subtitle_download.download_video https://www.youtube.com/watch?v=xxx          # 单个
uv run python -m subtitle_download.download_video url1 url2 url3                               # 多个
uv run python -m subtitle_download.download_video -f urls.txt --workers 5                      # 列表提速
uv run python -m subtitle_download.download_video "https://www.youtube.com/playlist?list=..."  # 播放列表
```

**验证步骤**：`--help` + aria2c 探测 → 单视频实测 → 列表 + 已存在跳过实测 → Ctrl+C 中断后续传验证

**你侧需要准备的**（不变）：网络可达 YouTube（必要时设 `HTTP_PROXY`/`HTTPS_PROXY`）；年龄限制/会员视频需要 cookies（可选）。

方案已定，请 **toggle to Act mode**，我开始写脚本并实测验证。

---

文件完整无误。任务完成，最终总结如下。

## 完成情况

**新建** `d:\AI_Projects\subtitle_download\src\subtitle_download\download_video.py`（347 行），并更新了 README。

### 功能实现（对照你的需求）
1. **yt_dlp + aria2c** ✅ — yt-dlp 负责解析/元数据，aria2c 做 16 连接分块传输（`--split=16 -x16 -k1M`，与项目现有播客下载器同款配置），ffmpeg 合并音视频
2. **多线程** ✅ — `ThreadPoolExecutor` 并发，每个任务独立 `YoutubeDL` 实例（yt-dlp 非线程安全）
3. **单个/多视频列表** ✅ — 三种输入全部支持：
   ```bash
   uv run python -m subtitle_download.download_video <URL>              # 单个
   uv run python -m subtitle_download.download_video <URL1> <URL2>      # 多个
   uv run python -m subtitle_download.download_video -f urls.txt -w 2   # 列表文件(#注释可跳过)
   uv run python -m subtitle_download.download_video "<播放列表URL>"     # 播放列表自动展开
   ```
4. **按你选的简单模式**：默认 `workers=2`，需要提速手动 `--workers 5`；防护措施齐备（启动随机间隔 3~8s、429 指数退避重试、归档跳过）

### 实测验证结果（本机 aria2c/ffmpeg/yt-dlp 真实联动）
| 测试 | 结果 |
|---|---|
| aria2c 自动探测 + `--version` 校验 | ✅ |
| 多 URL 并发下载（workers=2，双 worker 日志可见） | ✅ 2/2 成功 |
| aria2c 并行分块（日志可见真实 Range 分块请求） | ✅ |
| `-f` 列表文件（含注释行） | ✅ mp4×2 |
| 重跑归档跳过（不重复下载） | ✅ 6.4s 完成、零新增 |
| `--audio-only` ffmpeg 提取 mp3（真实音频文件） | ✅ mp3×1 |
| 失败处理 + 汇总 + 退出码 | ✅ |

**测试中发现并修复 1 个真实缺陷**：音频/不同画质若共用一个归档文件，切换模式后会“假跳过”——已改为按模式/画质独立归档（`download_archive_audio.txt` / `download_archive_1080.txt`）。

### 需要你知道的限制
- **本机当前无法直连 YouTube**（curl 实测超时），所以 YouTube 真实下载和播放列表展开**未实测**。代码路径与实测路径一致，但请开代理/VPN 后实测；被墙时设 `HTTP_PROXY`/`HTTPS_PROXY` 即可，脚本自动透传
- 测试用的临时媒体文件已清理，`work/` 下保留了 4 个测试脚本（`test_local*.py`，本地 HTTP 服务模拟下载源，可随时复跑）

### 你还需要的东西（最终答案）
什么都不用装。唯一待办：**确认网络能访问 YouTube**（必要时开 VPN/代理）；下载年龄限制/会员视频才需要 `--cookies-from-browser chrome`。