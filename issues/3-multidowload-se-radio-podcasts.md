全部完成 ✅

## 问题诊断

运行结果只有 4 个 `untitled.mp3` 有两个原因：

1. **字段名不匹配（untitled 命名的根源）**：`download_one` 用 `item.get("page")` 推导文件名，但你的 JSON 数据里字段是 **`link`**，没有 `page`。所以每条记录都得到空 slug → 全部命名为 `untitled.mp3`。
2. **并发竞态（只有 4 个文件的根源）**：JSON 实际有 **742 条**，默认 5 个并发 worker 同时启动，都发现 `untitled.mp3` 不存在，一起下载写同一个临时文件；完成后被 `_safe_name` 依次改名为 `untitled.mp3`、`untitled_1.mp3`……随后所有条目发现 `untitled.mp3` 已存在，全部被误判为“已存在”跳过。

## 代码修改（`src/subtitle_download/download_podcasts_se_radio.py`）

1. **新增 `_filename_from_url()`**：优先从 **mp3 URL 路径最后一段**取文件名（去查询参数 `?dest-id=23379`、URL 解码、去非法字符、无扩展名自动补 `.mp3`）
2. **文件名回退链**：mp3 URL basename → `page`/`link` slug → `untitled`（并修正了读取 `link` 字段）
3. **并发防护**：加 `_g_inflight` 集合 + 锁，同一文件名不会被两个线程同时下载
4. **断点续传改进**：存在 `.aria2` 控制文件时不跳过（说明没下载完）；Ctrl+C 中断时保留 `.aria2` 以便下次续传（之前会把控制文件删掉，导致残留的半截文件被误判为已完成）

## 验证结果

- 单元验证：`https://traffic.libsyn.com/secure/seradio/737-owen-mcgirr-software-accessibility.mp3?dest-id=23379` → **`737-owen-mcgirr-software-accessibility.mp3`** ✓（还测了无扩展名补 `.mp3`、URL 中文解码、空 URL 回退）
- 端到端实测：真实下载了 737 这一集，文件名正确；重跑同一条 → 正确显示 `[SKIP] 已存在`
- **已把之前下载的 4 个 untitled 文件按远程 Content-Length 匹配重命名**（避免重新下载 ~330MB），并删除了 737 的重复文件。`podcasts/` 目录现在是：
  - `733-max-corbridge-securing-ai-agents.mp3`
  - `735-vivek-yadav-regression-testing-microservices.mp3`
  - `736-sahil-walia-apache-iceberg.mp3`
  - `737-owen-mcgirr-software-accessibility.mp3`

现在重新运行原命令即可下载全部 742 条，文件名都会来自 mp3 URL，已有的 4 条会自动跳过：

```
python -m subtitle_download.download_podcasts_se_radio data\se_radio_23379.json
```

临时测试文件已全部清理。