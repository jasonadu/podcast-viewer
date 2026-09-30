dist\software-engineering-at-google\chapter-01.html
实现中文段落加载后自动隐藏，点击英文段落显示或隐藏中文段落

另外设置一个悬浮的按钮，一键切换全部英文，或者中文

---

已完成并验证。下面是这次改动的完整说明。

## 做了什么

**1. 先摸清结构（所有 28 个页面）**

| 项 | 结论 |
|---|---|
| 页面骨架 | `<style>` → `</head>` → `<body><button id="menubtn">☰</button><nav id="toc">` … `<main>` … `</main>` → `<script>` → `</body></html>`（每个锚点全文件唯一）  |
| 中英对照 | 正文是紧邻兄弟节点 `<p class="en">…</p>` + `<p class="zh">…</p>`，chapter-01 为 121/121 完全配对 |
| 例外 | **chapter-03 有 8 个、chapter-07 有 62 个 `p.zh` 没有对应英文**（英文是表格单元格里的裸文本），这些若隐藏就会**丢内容** |
| 编码 | UTF-8 无 BOM、LF 换行、都定义了 `--zh-bg/--accent/--line/--ink` |

**2. 新增 `tools/patch_bilingual_toggle.py`**（沿用已有 `tools/patch_toc_toggle.py` 的写法：argparse、`--dry-run`/`--dir`/`--file`、幂等、中文文档串）。每页插 4 处：

- **CSS（`</style>` 前）**：`p.zh{display:none}` —— 中文段落解析即隐藏，**首帧不闪**；`.zh-keep` 让「没有英文对应的中文」常显；`html.lang-zh/.lang-both` 两个模式类；`p.en.en-paired:hover` 浅蓝高亮提示可点；右下角悬浮胶囊按钮 `#langbtn`（窄屏 `z-index:80`，让滑出的目录盖住它）。
- **head 抢占脚本（`</head>` 前）**：翻页前先把 `localStorage` 里的模式还原到 `<html>`，避免切换页面时中文闪一下。
- **悬浮按钮**：紧跟在 ☰ 按钮后、`<main>` 之外。
- **尾部脚本（`</body>` 前）**：配对相邻的 `p.en`/`p.zh`、给无英文对应的 `p.zh` 打 `.zh-keep`、在 `<main>` 上做事件委托（点英文段落 → 显示/隐藏它的中文；点链接或正在划词时不误触）；悬浮按钮在 **仅英文 → 仅中文 → 中英对照 → 仅英文** 间循环并写入 `localStorage['seag-lang-mode']`，换模式时清掉单段的手工状态。

**3. 已应用到 `dist/software-engineering-at-google/` 全部 28 个页面**（你点名的 `chapter-01.html` + 其余章节，否则翻到第 2 章功能就没了）。`git diff --numstat` 每文件均为 **+121 / -1**，那 1 行删除只是被新按钮拆开的原行，**没有任何内容被删改**。

**4. 新增 `tools/verify_bilingual_toggle.js`** —— 用最小 DOM 桩 + `node:vm` **真实执行页面里内嵌的脚本**（并对页面 CSS 层叠做了近似），跑真实 HTML 做回归验证：

```
node tools/verify_bilingual_toggle.js "dist/software-engineering-at-google/*.html"
→ ok=1008  fail=0  文件=28   ===== 全部通过 =====
```

覆盖：加载即隐藏、点击切换、三模式循环、localStorage 记忆 + 首帧恢复、点链接/`<code>` 不误触、无英文对应的译文保持可见（chapter-03 的 8 条、chapter-07 的 62 条）。另外验证了幂等：重跑补丁工具 → 28 个全部 `skip`。

## 使用方式

直接在浏览器打开 `dist/software-engineering-at-google/chapter-01.html`：中文段落默认不显示 → 点英文段落（悬停会有浅蓝块 + 提示「点击显示 / 隐藏中文」）展开或收起该段译文 → 右下角 🌐 悬浮按钮一键在「仅英文 / 仅中文 / 中英对照」间循环，翻页后保持不变。

- 撤销全部：`git checkout -- dist/software-engineering-at-google`
- 只补一个文件：`python tools/patch_bilingual_toggle.py --file dist/software-engineering-at-google/chapter-01.html`
- 重新验证：`node tools/verify_bilingual_toggle.js "dist/software-engineering-at-google/*.html"`

## 明确的取舍（需要的话我可以再改）

1. **只管 `p.en` / `p.zh` 段落**。标题里的 `<span class="hz">` 和 `<figcaption>` 里的中文在所有模式下都保持中英双语 —— 各章标题的 DOM 写法不一致（有 `<h1>…<span class="hz">`、`<div class="hz">` 等多种），强行包裹容易出错。如果你要「仅中文」时标题也只留中文，我可以用 JS 把标题里的英文文本节点包成 `<span>` 再隐藏。
2. **chapter-07 表格**里的中文没有对应英文段落，所以「仅中文」模式下那些英文小字仍会显示 —— 这是为了避免丢内容，不是漏做。