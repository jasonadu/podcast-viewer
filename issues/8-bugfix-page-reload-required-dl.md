# bug: page reloaded required when dl 

(1/17) [error] ERROR: [youtube] 3FlHmImpAns: The page needs to be reloaded.
[FAIL]  无法解析 https://www.youtube.com/watch?v=3FlHmImpAns: ERROR: [youtube] 3FlHmImpAns: The page needs to be reloaded.

---

修复已完成并验证。以下是完整总结：

## 问题原因

失败链路（通过 yt-dlp `2026.08.19` 源码逐层确认）：

1. YouTube 现在对格式 URL 施加 **n-signature JS 挑战**，yt-dlp 需要 JS 运行时（你的机器上 deno 2.8.1 ✅ 已装）配合**官方挑战求解脚本**才能解开。
2. 求解脚本属于 yt-dlp 的"远程组件"（`ejs:github` = 从 GitHub Releases 下载脚本、`ejs:npm` = deno 需要的 npm 依赖），**默认是禁用的**。你日志里的关键警告正是这个意思：
   ```
   [jsc] Remote components challenge solver script (deno) and NPM package (deno) were skipped
   ```
3. 没有求解脚本，yt-dlp 只能用内置旧版脚本 → `n challenge solving failed` → 拿到的格式 URL 无效 → YouTube 返回 `ERROR: The page needs to be reloaded.` → `[FAIL] 无法解析`。

yt-dlp 在警告里自己给出了修复方案：启用 `--remote-components ejs:github`（Python API 对应 `remote_components` 参数），而你的脚本两处 yt-dlp 选项都没设置。

## 修改内容（`d:\AI_Projects\subtitle_download\src\subtitle_download\download_video.py`，净增 7 行）

1. **新增常量**（第 73–77 行）：
   ```python
   REMOTE_COMPONENTS = ('ejs:github', 'ejs:npm')
   ```
2. **解析阶段** `expand_playlist()` 的 ydl_opts 注入 `'remote_components': list(REMOTE_COMPONENTS)`
3. **下载阶段** `build_ydl_opts()` 的 ydl_opts 注入同一参数

（`git diff` 已确认改动仅此三处；README.md 的 diff 是你之前已有的未提交修改，未动。）

## 验证结果

| 验证项 | 结果 |
|---|---|
| 语法检查（`ast.parse`） | ✅ 通过 |
| 离线验证：两组选项构建 `YoutubeDL` 实例 | ✅ `params['remote_components'] = ['ejs:github', 'ejs:npm']` 正确生效 |
| 在线验证：重跑之前失败的 `3FlHmImpAns` | ✅ 关键行为转变——从"**were skipped**"（跳过求解脚本）变为实际执行 `[jsc:deno] Downloading challenge solver lib script from https://github.com/yt-dlp/ejs/releases/download/0.8.0/yt.solver.lib.min.js`，即修复生效的直接证据 |

**说明**：端到端完整解析未跑完，原因是当时代理网络不稳定（日志可见 `RemoteDisconnected` 重试）加上我这边终端命令有超时限制连带终止了后台进程——不是代码问题。修复逻辑与 yt-dlp 官方警告给出的方案完全一致。

## 重跑时注意事项

- **首次运行需要能访问 github.com**（走你访问 YouTube 的同一代理）下载求解脚本；成功一次后 yt-dlp 会缓存到 `C:\Users\Administrator\.cache\yt-dlp`，之后离线也能用。
- 直接重跑原命令即可，无需其他改动：
  ```
  python -m subtitle_download.download_video -f data\youtube-list.txt --cookies data\www.youtube.com_cookies.txt
  ```
- 如果 GitHub 下载仍然很慢/失败，替代方案是安装本地包 `pip install yt-dlp-ejs`（yt-dlp 会优先用它，连远程下载都省了），但需要匹配 yt-dlp 2026.08.19 的版本。

---

# 为什么先进行链接全部解析？

验证完毕，所有证据都齐了。下面是完整解析：

## 结论：这是脚本设计的正常行为，不是故障

`Solving JS challenges using deno` 属于 **[2/4] 解析阶段**的工作，而**下载被设计为在全部 17 个 URL 解析完、并通过归档预检查之后才开始**。你看到的 log 说明解析正在顺利推进（而且上次修的 "The page needs to be reloaded" 错误已经消失了）。

## 脚本的四阶段流水线（`main()`，第 651–727 行）

```
[1/4] 初始化        ← 已完成 (0.2s)
[2/4] 解析输入      ← 你现在的 log 停在这里：串行解析 17 个 URL，只收集标题和 id
[3/4] 归档预检查    ← 用解析到的 id 对照归档文件，筛掉已下载的
[4/4] 并发下载      ← 到这里才启动线程池 executor.submit(download_one, ...)
```

关键代码逻辑（[2/4] 阶段，第 657–679 行）是一个 **串行 for 循环**：逐个调用 `expand_playlist_with_retry()` → `yt_dlp.extract_info(download=False)`，每个视频只拿 `title`/`id` 加入任务列表 `tasks`，**不下载任何媒体字节**。只有当 17 个 URL 全部处理完，才会打印 `[2/4] 完成`，然后进入 [3/4] 和 [4/4]。

**为什么必须先解析完全部才下载？** 模块开头的 docstring 写明了设计意图：

> 归档预检查（两级，都发生在进入线程池之前）……已归档的既不占用 worker 名额，也不会拖到下载阶段才发现"归档中已存在"。

即 [3/4] 阶段需要**所有视频的 id** 才能判定哪些已在 `download_archive_1080.txt` 里，所以线程池要等解析全部结束才启动。

## 为什么解析阶段也会出现 "Solving JS challenges using deno"

这是上次修复（`remote_components`）生效后的正常一环。你可能会想：解析不是设了 `extract_flat='in_playlist'` + `skip_download=True` 吗，为什么还要解 JS 挑战？

我核对了 yt-dlp 源码 `YoutubeDL.py:1927-1929`：

```python
extract_flat = self.params.get('extract_flat', False)
if ((extract_flat == 'in_playlist' and 'playlist' in extra_info)   # ← 只对播放列表条目生效
        or extract_flat is True):
```

`extract_flat='in_playlist'` **只对"来自播放列表的条目"生效**。你的 17 个 URL 都是单个视频链接，没有 playlist 上下文，所以 yt-dlp 对每个视频执行**完整提取**：

```
Downloading webpage → player JS → web/tv player API JSON
→ [jsc:deno] Solving JS challenges using deno   ← 解 n-signature 挑战（解析的一部分）
→ 返回 title/id/formats 元数据（不下载媒体字节，因为 skip_download=True）
```

`skip_download=True` 只跳过"下载媒体字节"这一步，元数据提取和挑战解算照做。所以每个视频耗时 ~30–55 秒 = 完整元数据提取 + deno 解挑战 + 代理网络延迟。

## 从 log 看，一切都在正轨上

| 观察点 | 说明 |
|---|---|
| 没有再报 `The page needs to be reloaded` | ✅ 上次的 `remote_components` 修复已生效 |
| (1/17) 花了 1m23s，有 `Downloading challenge solver lib script from github.com/yt-dlp/ejs/...` | 首个视频要下载官方求解脚本（之后会缓存），所以最慢 |
| (2/17)~(5/17) 每个 32–55s，都成功返回标题（`└ 单个视频: ...`） | 有缓存后稳定了解挑战，解析在正常推进 |
| 17 个 URL 串行解析 | 预计共约 10 分钟；结束后会依次出现 `[2/4] 完成` → `[3/4] 归档预检查` → `[4/4] 并发下载` |

**你不需要做任何事，等它跑完 [2/4] 就会自动进入下载。** 你归档里已有 30 条记录，本次 17 个 URL 中已下载过的会在 [2/4] 开头（URL 级）或 [3/4]（id 级）被 `[SKIP]` 掉，真正下载的只会是缺失的那些。

## 顺带说明（如果你觉得解析慢）

阶段 [2/4] 是**有意串行**的——`--workers` 参数只作用于 [4/4] 下载阶段，解析阶段并发过高容易触发 YouTube 429 限流（README/issue 里记录过这个坑）。单视频的 id 虽然能直接从 URL 提取（代码里 `youtube_id_from_url` 已经这么干用于归档秒跳过），但**标题必须联网拿**，这一步省不掉，除非接受把标题解析推迟到下载阶段做（会改变现有日志和归档预检查的形态，属于另一个优化话题，需要的话可以再讨论）。

---

# 解析功能的断点续传

全部完成并验证通过。最终代码复查确认无误，总结如下：

## 实现内容：[2/4] 解析阶段的断点续传缓存

### 工作方式

**每次成功解析一个 URL 后立即追加写入** `videos\parse_cache.jsonl`（JSONL，每行一条记录，含 `url/ts/items`）。网络中断后重跑时：

```
[info] [1/4] 初始化: ...
[info]   └ 解析缓存: videos\parse_cache.jsonl (12 条, 有效期 7 天)   ← 新增日志
[info] [2/4] 解析输入: 17 个 URL (归档已存在或解析缓存命中的不联网)
[CACHE] (1/17) https://www.youtube.com/watch?v=3FlHmImpAns (命中解析缓存: 【アーカイブ動画】...)  ← 秒过, 不联网
...
[info] [2/4] 完成: ... 归档跳过 3 个 URL, 解析缓存命中 12 个, 用时 45s   ← 新增统计
```

**中断最多只损失最后一个未写入的 URL**（追加写 + flush），其余全部复用。

### 关键设计

| 决策 | 说明 |
|---|---|
| 缓存格式 JSONL 追加写 | 对中断天然鲁棒：损坏的半行读取时直接丢弃，不影响其他条目 |
| 默认 TTL 7 天（`--parse-cache-ttl` 可调，`0`=永不过期） | 防止播放列表 URL 因缓存看不到新条目；单视频标题/ID 基本不变，7 天足够覆盖断网重跑场景 |
| `--no-parse-cache` | 强制全部联网刷新（如需立即看到播放列表新增条目） |
| 过期/损坏条目自动清理 | 加载时顺带重写压缩文件，防止无限增长 |
| 检查顺序 | 归档跳过（[SKIP]）优先于缓存命中（[CACHE]），归档预检查 [3/4]、下载 [4/4] 逻辑零改动 |

### 修改的文件

1. **`src/subtitle_download/download_video.py`**（+141/-2 行，含本次会话累计）：
   - 新增 `ParseCache` 类、`PARSE_CACHE_FILENAME` / `DEFAULT_PARSE_CACHE_TTL_DAYS` 常量、`import json`
   - `main()`：[1/4] 初始化缓存并打印状态、[2/4] 循环集成命中/写入、完成行统计命中数、两个新命令行参数、docstring 更新
2. **`work/test_parse_cache.py`**（新增，保留作为回归测试）：7 个离线测试，全程 mock 网络与下载

### 测试中发现并修复的 bug

首轮测试 7 个用例挂了 4 个——最小化复现发现 `add()` 写入的 JSON **漏了 `url` 字段**（URL 只作内存字典键，没写进记录），导致 `_load()` 读取时 `KeyError` 把所有条目当损坏行丢弃。已修复为写入完整记录 `{'url', 'ts', 'items'}`。

### 最终验证结果

```
test_cache_hit_on_rerun .............. ok    ← 核心场景: 重跑零联网、2 个 [CACHE] 命中
test_no_parse_cache_forces_reparse ... ok    ← --no-parse-cache 强制刷新
test_add_get_roundtrip ............... ok
test_corrupt_half_line_dropped ....... ok    ← 中断残留半行安全丢弃
test_invalid_items_filtered .......... ok
test_playlist_items_preserved ........ ok    ← 播放列表整体缓存
test_ttl_expiry_compacts_file ........ ok    ← TTL 过期 + 文件压缩

Ran 7 tests — OK
```

**使用提示**：直接重跑原命令即可，无需任何参数变化；若想让缓存永不过期可用 `--parse-cache-ttl 0`，强制刷新全部解析结果用 `--no-parse-cache`。