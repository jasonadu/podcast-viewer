修改 @/main.py 
多线程（默认3个）下载YouTube视频的字幕。

- 视频url数据位于 @/data/comp/up.txt 
格式如下：title | 视频url
```
1. SE Radio 737: Owen McGirr on Software Accessibility | https://www.youtube.com/watch?v=hvufdIjFYsM
```
- 字幕文件命名方式
按照上面的视频的title在文件 @/data/se_radio_23379.json
查找对应的mp3地址，从中提取mp3的名字，作为字幕名字
比如 737-owen-mcgirr-software-accessibility.srt 的对应mp3地址是
"mp3": "https://traffic.libsyn.com/secure/seradio/737-owen-mcgirr-software-accessibility.mp3?dest-id=23379",

- 字幕文件保存在目录 @/subtitles 
- 安全措施：每个下载完成后，随机等待1~2秒

---

The test file is cleaned up. The implementation is complete and verified.

## Summary

I modified `src/subtitle_download/main.py` to implement multi-threaded YouTube subtitle downloading. Here's what was done:

### Key Design Decisions

1. **Title Matching Strategy** (two-tier):
   - **Primary**: Extract episode number from `up.txt` title using regex patterns (handles `SE Radio 737:`, `Episode 230:`, `SE-Radio-Episode-234:-`, `549-slug-`, etc.), then look up by `index` in the JSON
   - **Fallback**: For titles without numbers (e.g., "Camille Fournier on Real-World Distributed Systems"), use keyword matching against JSON titles

2. **mp3→srt naming**: Take basename of mp3 URL path, strip `.mp3` and query string (`?dest-id=23379`), append `.srt` — e.g., `737-owen-mcgirr-software-accessibility.srt`

3. **Duplicate handling**: JSON has duplicate indices (725, 718, 714) — first match wins

4. **Non-SE-Radio entries**: Skipped with log (e.g., `omegatau-60-...`)

### Implementation Details

- **`ThreadPoolExecutor(max_workers=3)`** for concurrent downloads
- **`random.uniform(1, 2)`** sleep after each download
- **Skip existing files** in `subtitles/` directory
- **VTT→SRT conversion** using existing pure-Python logic (no ffmpeg needed)
- **Thread-safe** progress output using `threading.Lock`
- Preserved original `download_subtitle_lib()` function for backward compatibility

### Verified Results

- **736 entries** parsed from `up.txt`
- **714 unique indices** loaded from JSON
- **705/736 entries matched** (95.8%) — unmatched are non-SE-Radio or unusual formats
- All title format variants correctly matched
- Naming convention verified against existing `subtitles/old/` files

### Usage

```bash
cd d:\AI_Projects\subtitle_download
.venv\Scripts\python.exe -m subtitle_download.main
python -m subtitle_download.main
```

---

解析一下 @/data/exec.log 分析一下问题在哪里。另外除了 600_william_morgan_kubernetes_sidecars_and_service_mesh 可以看到字幕其他的看不到。

全部完成，临时文件与测试服务器已清理。以下是总结。

---

## 已完成的工作

### 问题诊断结论（回顾）

1. **日志失败原因**：`HTTP 429` 是 YouTube 对本机 IP 的限流（并发 3 + 1~2 秒等待节奏太快）；`no subtitle generated` 大部分是 429 的伪装（yt-dlp 字幕下载失败不抛异常，代码只能落空），少数老集数确实无英文字幕。
2. **“只有 600 能看到字幕”**：`dist/se_radio_viewer.html` 是 9/13 的旧构建，只内嵌了当时唯一存在的 600 号字幕；9/14 批量下载的 500+ 字幕没有进 HTML。

### 本次改造：字幕改为“播放时动态加载”（不再内嵌）

**`d:\AI_Projects\subtitle_download\tools\build_viewer.py`**
- 构建时只生成“哪一集有哪个 srt”的索引（`"sub": 文件名`），**不再解析/内嵌字幕内容**；构建产物从内嵌几十 MB 的规模降到 733 KB
- 新增 `__SUB_BASE__`（`../subtitles/` 相对路径）与 `__SUB_COUNT__`（字幕节目数）注入模板

**`d:\AI_Projects\subtitle_download\tools\viewer_template.html`**
- 新增浏览器端 `parseSrt()`（SRT→cues，支持 BOM/CRLF/毫秒逗号/去内联标签）和 `loadCues()`（点开播放器时 `fetch` 对应 srt，结果按文件名缓存，二次打开瞬时显示）
- 字幕区状态机：加载中 → 就绪 /（无字幕）/ 加载失败；`file://` 直开时给出明确的操作提示
- **修复了一个真实缺陷**：YouTube 自动字幕清理后遗留的 10ms 级碎片块（与下一条首尾相接，延长 end 会被二分查找跳过）——改为把碎片文本**并入紧随其后的正常块**（ep173 合并 39 个、ep600 合并 4 个碎片）

**`d:\AI_Projects\subtitle_download\README.md`** — 修正运行命令、新增浏览器用法（必须经本地 HTTP 服务打开）与 429 缓解说明。

### 验证结果

| 验证项 | 结果 |
|---|---|
| 重建 dist：`字幕索引 730 个匹配键 · 含字幕节目 663 条 · √ 自检通过 · RC=0` | ✅ |
| dist 内容：`cues` 内嵌 0 处、`sub` 键 663 处、`SUB_BASE='../subtitles/'` | ✅ |
| HTTP 路径（临时起 `http.server` 实测）：viewer 200、ep173/ep600 srt 200 | ✅ |
| `parseSrt` 单测（Node 直接提取模板函数 + 真实 srt）：14/14 PASS | ✅ |
| 临时文件/测试服务器清理 | ✅ |

### 使用方式（重要）

浏览器禁止 `file://` 页面读取本地文件，请这样打开：

```bash
cd d:\AI_Projects\subtitle_download
python -m http.server 8000
# 浏览器访问 http://localhost:8000/dist/se_radio_viewer.html
```

之后下载了新字幕只需重跑 `python tools/build_viewer.py` 刷新索引（秒级）。

### 遗留事项

`main.py` 的 429 对抗改造（降并发为 1、加大随机等待、指数退避、支持 cookies、失败清单）尚未实施——等你说明网络环境（是否已有代理/VPN、能否取浏览器 cookies）后我再做，方案不受本次 viewer 改造影响。