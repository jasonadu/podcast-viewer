# 问题
1. 修改 @/src/subtitle_download/download_video.py 
"归档中已存在等情况视为跳过" 修改这个逻辑为多线程下载之前进行。
2. 检查进度长时间停止在日志“[info] 归档文件: ”，如果有问题修复一下，每一步要给出明确的当前进度
。

---


All checks pass and the workspace is clean. Here's the summary of what I did.

## 1. 归档「已存在视为跳过」→ 移到多线程之前 ✅

原逻辑在 `download_one()` 里（每个 worker 下载时才发现 `has already been recorded`），现在改为**线程池启动之前**的第 3 阶段：

- 新增归档三个函数（`download_video.py` 325–405 行）：
  - `make_archive_id(extractor_key, video_id)` —— 与 `yt_dlp.utils.make_archive_id` 完全一致（`<extractor 小写> <id>`）。
  - `parse_archive_file(path)` —— 读归档文件，返回（完整行集合, 视频 id 集合），兼容旧版单列格式。
  - `split_archived(tasks, ...)` —— 优先用 yt-dlp 自己的 `ydl.in_download_archive()` 判定（语义 100% 一致），异常时回退本地解析；返回 `(待下载, 已归档, 无法预检查)`。
- `expand_playlist()` 现在返回带 `id`/`extractor_key` 的任务字典（播放列表 `extract_flat` 里顺手就有 id，单个视频解析时也有），预检查才有依据。
- 主流程第 3 阶段在 `ThreadPoolExecutor` 之前执行，逐个打印 `[SKIP] xxx (归档中已存在)` 并汇总「已下载跳过 N 个，待下载 M 个」；**全部已归档时直接秒退（退出码 0）**，不占 worker、不发请求。`download_one` 里保留了兜底跳过（给拿不到 id 的条目，预检查会提示「无法预检查 N 个」）。

## 2. 定位并修复「卡在 `[info] 归档文件:`」✅

**根因（两个）**：① 该日志之后是*静默的网络阻塞*——逐个 URL 做 yt-dlp 元数据/播放列表解析，`quiet=True` 把所有内部信息吞掉了，429/网络重试期间可以长时间零输出；② 更要命的是 **aria2c 作为外部下载器只回调一次 `finished`，没有任何增量进度回调**（我查过 `yt_dlp/downloader/external.py` 确认），所以整个下载期（大文件可能几十分钟）+ ffmpeg 合并期都是完全静默的——看起来就是卡死。

**修复**：

| 改进 | 说明 |
|---|---|
| 4 阶段编号进度 | `[1/4] 初始化` → `[2/4] 解析输入` → `[3/4] 归档预检查` → `[4/4] 并发下载`，每步带明细与耗时；`归档文件: ... (已有 N 条记录)` 让用户看到这一步立刻有反馈 |
| `Heartbeat` 心跳 | 阻塞步骤超 `HEARTBEAT_INTERVAL=15s` 就打印 `... 仍在进行, 已用 30s, 临时文件 2.7MiB`；用于解析每个 URL、每个下载任务（含 ffmpeg 合并） |
| `YdlLogger` 日志桥 | 把 yt-dlp 的 info/warning/error 带 `[worker-N] (3/20)` 前缀转发（`Extracting URL`/`Destination:`/`Merging`/`Deleting original file`/429 重试现在都可见），并过滤高频噪音（分片、进度重绘） |
| 进度行改造 | 全局共享的 `\r` 单行进度（多 worker 会互相覆盖/串行，A/B 实测会与 yt-dlp 输出粘成一行）→ 改为**每任务独立**、每 `PROGRESS_INTERVAL=5s` 一行的明确进度；每任务带 `(idx/total)`，每完成一个打印 `[info] 进度 3/20: 成功 2 / 跳过 1 / 失败 0` |
| 解析阶段 429 退避 | 新增 `expand_playlist_with_retry()`（8s→16s→32s），不再一次限流就丢掉整个 URL |
| 有界等待 | 解析加 `socket_timeout=20`（与 `youtube_list.py` 一致）、下载 `noprogress=True` 关闭 yt-dlp 自带重绘 |

## 实测验证（都在本机真实跑通）

| 测试 | 结果 |
|---|---|
| `py_compile` / 导入 / `--help` | ✅ |
| `work/test_local.py`（已修：原来用随机字节假冒 mp4，ffprobe 解不开导致 audio-only 假失败；现用 ffmpeg 生成真实 mpeg4+aac） | ✅ **全部通过**：并发 2 URL 成功、预检查跳过 1 次仅 **1.5s**、`--audio-only` 产出 mp3 |
| `work/test_heartbeat.py`（新增：限速 100KiB/s 服务，3MiB 下载 ~38s） | ✅ **通过**：心跳 2 行（15s/30s，含临时文件大小），rc=0 |
| A/B 对照（`git show HEAD` 的原始版本 vs 现版本，同一条慢速 URL） | 原始：**0 心跳、40s 全程静默**；现版本：**2 心跳 + 带前缀的清晰输出** |
| 播放列表分支 + 归档预检查单元校验（假 YoutubeDL，不联网） | ✅ `PLAYLIST_CHECK_OK`（URL/标题/id 正确，`youtube abc…` 归档行能预检查命中） |

## 其他改动

- `README.md` 视频下载小节 +3 条说明（归档预检查、进度可见性、解析阶段 429 退避）。
- 行尾：三个被改文件的 CRLF 风格已还原（`bare_lf=0`）；`git diff --numstat` 仅 `README.md(+3)` 与 `download_video.py(409+/70-)`，未触碰其他文件。
- 临时验证脚本已清理，只保留 `work/test_local.py`（更新）与 `work/test_heartbeat.py`（新增）供以后回归。

## 需要你知道的两点（都是既有现象，不是本次引入）

1. 下载时会出现约每秒一行的**纯空白行**——A/B 对照显示原始版本同样有（28 行 vs 27 行），是 yt-dlp 自身对进度行的清除写入，终端里是原地重绘，重定向到文件才看得见。
2. aria2c 自己的 ERROR 级 stderr（例如本地测试服务器不支持 Range 时）由 yt-dlp 原样转发到 stderr，**不走我们的加锁/前缀**（yt-dlp 行为），真实 YouTube 场景基本不会出现。
3. 本机仍无法直连 YouTube，所以 YouTube 真实下载/播放列表未实测——但播放列表解析→任务生成→归档预检查→下载这条链路已用 generic 提取器 + 假提取器分别验证。

---

## 从日志看并没有跳过已经下载的文件啊

---

已修复——你这个反馈完全命中要害。原因是我把预检查放在了 **第 3 阶段（全部 URL 解析完之后）**，所以你用 20 个 URL 时必须先等 20 次联网元数据提取，才会看到第一条 `[SKIP]`。现在改成**两级预检查**：

## 改动：归档跳过改成「解析前就跳过」

| 级别 | 位置 | 行为 |
|---|---|---|
| ① URL 级（新） | 第 2 阶段，逐 URL 循环开头 | 单个视频的 id **直接从 URL 字符串里取**（`youtu.be/<id>`、`watch?v=`、`/shorts/`、`/embed/`、`/live/`），归档中已有就立刻打印 `[SKIP] (1/20) <url> (归档中已存在, 未联网解析)` 并 `continue`——**零网络请求、零等待、不进线程池** |
| ② 解析后 | 第 3 阶段，线程池之前 | 播放列表展开出的条目（以及 URL 里取不到 id 的）解析完再统一判定，同样打印 `[SKIP]`；全部已归档则秒退（退出码 0） |

新增的代码：`youtube_id_from_url()`（正则，不联网）、`is_playlist_url()`（带 `list=` 的 URL 仍按播放列表展开，避免漏掉列表里的其它视频）、`ArchiveIndex` 类（一次读归档，复用 yt-dlp 的 `in_download_archive()` 判定；失败回退本地解析 `'<extractor> <video_id>'`）。`split_archived()` 也顺带增强：任务没带 id 时，从 URL 里再取一次 id 来提高预检查覆盖率。

你那边重跑后应该看到这样的序列（20 个 URL 里已下载的会瞬间刷完）：

```
[info] [2/4] 解析输入: 20 个 URL (归档中已存在的会先跳过, 不联网解析)
[SKIP]  (1/20) https://youtu.be/2Y1Z6u_5Arw (归档中已存在, 未联网解析)
[SKIP]  (2/20) https://www.youtube.com/watch?v=xxxxxxxxxxx (归档中已存在, 未联网解析)
...
[info] [2/4] 完成: 待检查 4 个视频, 归档跳过 16 个 URL, 用时 2.1s
[info] [3/4] 归档预检查 (线程池启动之前): videos\download_archive_1080.txt
[info] [3/4] 完成: 归档跳过合计 16 个 (URL 阶段 16 + 解析后 0), 待下载 4 个, 用时 0.0s
```

> 注意：跳过判定用的是归档行 `youtube <11位id>`，与 yt-dlp 自己写的格式完全一致；你日志里显示的 `videos\download_archive_1080.txt (已有 29 条记录)` 就是判定依据。若某个归档条目的画质/模式与当前参数不同，它属于另一个归档文件（这是原本的按画质/模式独立设计）。没被归档的新视频仍会联网解析，期间那 15s 一行的心跳就是"还活着"的信号，429 也会自动退避重试（8s→16s→32s）而不是丢掉 URL。

## 验证（都是本机真实跑通）

- 给 `work/test_local.py` 新增 **test4**：把 `youtube 2Y1Z6u_5Arw` 写进归档，再跑 `https://youtu.be/2Y1Z6u_5Arw` → 输出
  `[SKIP] (1/1) https://youtu.be/2Y1Z6u_5Arw (归档中已存在, 未联网解析)` / `全部 1 个 URL 都已在归档中, 无需下载 (总用时 0.1s)` / **rc=0，用时 0.7~0.8s**（进程启动+判重，全程无网络）✅
- `work/test_local.py` 4 项全过（并发下载、归档预检查跳过、audio-only 出 mp3、URL 级零请求跳过）→ `[test] 结果: 全部通过` ✅
- `work/test_heartbeat.py` 重跑：37.1s 限速下载期间 2 条心跳、rc=0 → `通过` ✅
- `py_compile` 通过；`download_video.py` 750 行、行尾 CRLF 一致（bare_lf=0）；`git diff --numstat` 仅 `README.md(+3)` 与 `download_video.py(462+/71-)`。

## 需要坦白的一处失误

在最后统一行尾的那一步，我写的一行式 Python 命令因为 `open(f,'wb')` 先执行导致 **README.md 与 `work/test_local.py` 被清空**。已完整恢复：README.md 用 `git checkout` 还原后重新加回 3 条说明；`work/test_local.py`（未被 git 跟踪）按原内容 + 本次修改完整重写，并跑通全部用例确认无遗漏。之后所有文件操作都改成"先整份读入变量再写回"并校验字节数。

顺带说明：`issues/6-youtube-dl-update.md` 这个未跟踪文件不是我创建的，我未改动它。