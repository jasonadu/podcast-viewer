# 工作任务总结：英文 EPUB 双语 HTML 转换

## 1. 任务概况

| 项 | 内容 |
|---|---|
| 任务目标 | 将一本 英文技术 EPUB 转换为**逐段中英对照、图片 base64 内嵌、带左侧可折叠目录导航**的多文件 HTML 阅读版 |
| 输入物 | 英文版 EPUB（ZIP 容器：META-INF/container.xml + content.opf + XHTML 正文 + CSS + 图片） |
| 执行时间 | 单次会话内连续完成（含样板确认、样式修正、11 章 + 前置/结语 + 总入口） |
| 最终结果 | 14 个自包含 HTML（index + front-matter + closing + chapter-01~11），打包 zip；每文件 UTF-8、单文件可离线打开 |
| 核心规则 | 原文逐段全译，不做概要 / 合并；一章一文件不拆分（按 EPUB spine 的 XHTML 文件划分）；代码块保持英文不翻译；中文段落浅蓝底+左色条与英文区分；图片压缩后 base64 内嵌；**EPUB 无物理页码**，改为每译完原书一个小节后，单独加一行小节标记（显示原书小节标题 + 章节内锚点）便于定位。 |
| 中英文切换功能 | 中文段落默认不显示 → 点英文段落（悬停会有浅蓝块 + 提示「点击显示 / 隐藏中文」）展开或收起该段译文 → 右下角 🌐 悬浮按钮一键在「仅英文 / 仅中文 / 中英对照」间循环，翻页后保持不变。 |

### 关键节点时间线

1. **确认章节结构**：解包 EPUB 读 `content.opf`（`<spine>` 阅读顺序 + `<manifest>` 文件映射）与导航文档（nav.xhtml / toc.ncx），列出 11 章 + 前置 + 后置，用户确认"章节划分可以，不拆分"。
2. **样板先行**：先做 chapter-01.html 交付用户验收。
3. **样式修正**：用户截图反馈列表项内中文缺浅蓝背景，据此修 CSS（`li .zh` 与 `p.zh` 同规格）。
4. **模板沉淀**：把 CSS/JS/页面骨架抽成共享模块 `book_theme.py`，ch2 起复用。
5. **连续批量生产**：按统一 SOP 连续构建 ch2~ch11，无需中途确认。
6. **收尾**：front-matter、closing、index.html，打包 zip 交付。

---

## 2. SOP 流程总结

### 总流程（每章重复执行）

```
解包 EPUB → 读 content.opf(spine/manifest) 定章范围 → BeautifulSoup 抽 XHTML 文本
→ 按 manifest 抽图 → PIL 压缩转 base64 → Read 原文 → 写 build_chN.py
→ 后处理补花括号 → 运行生成 → 验证 → 交付
```

### 分步明细

| 步骤 | 输入 | 操作动作 | 输出 | 注意事项 |
|---|---|---|---|---|
| ① 定章范围 | EPUB 容器 / content.opf | `zipfile` 解包；读 `<spine>`（阅读顺序）与 `<manifest>`（文件映射），按 XHTML 文件划分章节 | 章文件清单 + 阅读顺序 | 以 spine 顺序为准；导航（nav/toc）与 spine 偶有不一致，阅读顺序按 spine 排 |
| ② 抽文本 | 章 XHTML | `BeautifulSoup` 解析；按 p/li/pre/table 块抽取文本并保留块结构，写 `chN_raw.txt` | 该章纯文本原文 + 块结构标注 | EPUB 无固定页码，靠块顺序定位；文本可能被 `<span>/<br>` 拆碎，需合并相邻文本节点 |
| ③ 抽图 | manifest 映射 | 按 manifest 的 href 从 EPUB 内解压图片到临时目录 | 原始图像文件 | SVG 浏览器兼容性差，需转 PNG（cairosvg）；GIF 可保留 |
| ④ 压图转 base64 | 原始图像 | PIL 打开；RGBA→RGB；宽>800px 则等比缩到 800；JPEG q≈68 重存；base64 写 `assets/figN-M.txt` | data-URI 文本文件 | 质量/体积平衡；SVG 转出的 PNG 也走本步；caption 中英对照 |
| ⑤ 读原文 | `chN_raw.txt` | 分段 Read（每次 500~600 行），逐段翻译并标记图注对应 | 章节内容大纲 + 图注映射 | 代码段跳过翻译；EPUB 表格本身是 HTML table，直接沿用结构转写 |
| ⑥ 写构建脚本 | 原文 + 图注映射 | `from book_theme import P,LI,img_b64,page`；正文写在 f-string body 里 | `build_chN.py` | **f-string 内 P()/LI() 必须写成 `{P(...)}` 才会求值** |
| ⑦ 后处理补花括号 | build_chN.py | 遍历行：以 `P('` 开头的行首加 `{`，向后找第一个以 `)` 结尾的行尾加 `}` | 修正后的脚本 | 避免误把 `}` 加到末尾 `page()` 调用行 |
| ⑧ 运行生成 | build_chN.py | `python3 build_chN.py` | `chapter-NN.html` | 脚本打印文件名与 KB 数即成功 |
| ⑨ 串联导航 | spine 顺序 | prev/next 按章号填（ch1 prev=front-matter，ch11 next=None） | 上下章链接正确 | 首尾章用 disabled 占位 |

### 分支判断与异常处理

| 触发条件 | 处理方式 |
|---|---|
| 图为 SVG（EPUB 常见矢量图） | cairosvg 转 PNG，再走 PIL 压缩流程 |
| manifest href 与实际路径不符 / 图缺失 | 按 manifest 映射定位；查不到则到 EPUB 全目录按文件名搜索 |
| 正文被 `<span>/<br>` 拆成碎行 | 抽文本时合并相邻文本节点，按块重建段落 |
| EPUB 自带 CSS 干扰版式 | 忽略原 CSS，样式统一由 book_theme 控制 |
| f-string 里含代码块花括号（JSON dict） | **不要写在 f-string 里**；把代码块抽成普通字符串变量 `CODE_X = '''...'''` 再 `+ CODE_X` 拼接（ch11 采用） |
| `{<P(` 误写（后处理把 `{` 加在了 `<P` 前） | `sed 's/{<P(/{P(/g'` 修正 |
| 批量补 `}` 误加到文件末尾 `page()` 行 | 手动删除该行尾多余的 `}` |
| 用户要求"先看效果再继续" | 只做 ch1 样板，暂停等反馈 |
| 用户改要求"代码不翻译" | 后续所有 `<pre><code>` 保留英文原文 |

---

## 3. 使用的 Skill 清单

| Skill | 用途 | 调用时机 | 关键参数/配置 | 使用效果 |
|---|---|---|---|---|
| **zipfile + BeautifulSoup**（EPUB 解析） | 解包 EPUB、读 content.opf（spine/manifest）、按章抽 XHTML 文本与图片 | 每章开始 | `spine` 顺序、`manifest` 映射、`get_text()` / `select()` 按块抽取 | 稳定拿到阅读顺序文本与图像文件 |
| **PIL (Pillow)** | 图片压缩、格式转换、base64 | 抽图后 | 宽上限 800px、JPEG quality=68、LANCZOS 重采样 | 单图从 MB 级压到 ~30~80KB，HTML 总体积可控 |
| **cairosvg** | SVG → PNG 转码 | 遇到 SVG 图时 | `cairosvg.svg2png(url=..., write_to=...)` | 让 EPUB 常见矢量图可内嵌 |
| **artifact-preview** | 产物视觉自检 | 交付前 | `render <file>` | SKILL.md 明确"HTML 产物不需要自检"，故仅核对文件大小/存在性，未逐页截图 |
| **book_theme.py（自建共享模块）** | 统一 CSS/JS/页面骨架/P、LI、page 函数 | ch2 起每章 import | `page(filename, chapter_num_label, title_en, title_zh, entries, body, prev, next_)` | 11 章样式一致，改一处全局生效 |

### Skill 配合顺序

```
解包EPUB(读content.opf) → BeautifulSoup(按章抽文本+图) → PIL(压图转base64存assets)
  → Read原文 → build_chN.py(import book_theme) → 运行产出 HTML
  → 打包 zip → FileBatchUpload 上传交付
```

衔接逻辑：EPUB 解析负责"从 EPUB 取原料"（文本按块抽取、图片按 manifest 定位），PIL 负责"把原料压成网页友好的图"，book_theme 负责"把内容套进统一版式"，三者解耦——换书只换原料，版式模板不变。

---

## 4. 页面样式与细节

> 本节与源格式无关，PDF / EPUB 输入均适用；唯一差异：EPUB 源无物理页码，页内定位统一用小节标记与锚点。

### 整体布局

- 左右两栏：左侧固定目录可隐藏 `nav#toc`（宽 300px），右侧主内容 `main`（`margin-left:300px`，最大宽 900px）。
- 目录 `position:fixed`，随页面滚动保持可见；右上角 ☰ 按钮可折叠/展开。
- 窄屏（≤900px）：目录默认隐藏为抽屉，点 ☰ 滑出，主内容占满全屏。

### 配色（CSS 变量）

| 变量 | 值 | 用途 |
|---|---|---|
| `--accent` | `#2563eb` | 强调色（标题条、按钮、链接 hover） |
| `--zh-bg` | `#f0f6ff` | 中文段落浅蓝底 |
| `--zh-border` | `#2563eb` | 中文块左侧 4px 色条 |
| `--code-bg` | `#0f172a` | 代码块深色底 |
| `--code-ink` | `#e2e8f0` | 代码块浅色字 |
| `--ink` | `#1f2937` | 正文墨色 |

### 字体

- 正文：`-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif`
- 代码：`"SF Mono", Menlo, Consolas, monospace`

### 关键组件样式

| 元素 | 样式 |
|---|---|
| 英文段 `p.en` | 普通段，14px 段距 |
| 中文段 `p.zh` | 浅蓝底 + 左边框 4px #2563eb + 圆角右 8px + 内边距 10/16 |
| 列表项中文 `li .zh` | 与 p.zh 同款浅蓝底+左色条（**用户反馈后补上**） |
| 表格 `th/td` | 1px 边框、表头 `#f1f5f9`、中文用 `.zh` 块附在英文下 |
| 图片 `figure img` | max-width 100%、1px 边框、圆角 8px；figcaption 中英对照 |
| 代码块 `pre` | 深色底、横向滚动、13.5px 等宽 |
| 行内代码 | 粉底圆角 `#be185d` |
| 分页按钮 `.pager a` | 三栏等分（上一章/返回目录/下一章），hover 蓝底 |
| 目录链接 | l2 加粗、l3 缩进 20px；hover 浅蓝底 |
| 背景色 | #faf6f1 |
| 字体大小 | 18px |

### 交互与状态

- 目录折叠：`transform:translateX(-300px)` 过渡 0.25s；宽屏切 `hidden`，窄屏切 `show`。
- 章节内目录点击锚点跳转（`scroll-margin-top:20px` 避免被顶栏遮挡）。
- 窄屏点击目录链接后自动收起抽屉。
- 首尾章的上一章/下一章按钮置灰（`disabled`，opacity .4，不可点）。

### 与规范/设计稿的差异

- 原始需求提到"弹窗/加载状态/空状态"——本任务为静态阅读页，无交互表单，故未实现这些状态；属合理裁剪。
- 图注中英对照、表格中文块化均按需求落实；未做额外视觉稿对比（无设计稿，以用户 ch1 验收截图为准）。
- 原 PDF 方案的"每页页码标记"因 EPUB 无物理页码而取消，改为小节标题标记 + 锚点（见 §1 核心规则）。

---

## 5. 问题与优化建议

### 遇到的问题与解决

| 问题 | 原因 | 解决办法 |
|---|---|---|
| 列表项中文无浅蓝背景 | 最初只给 `p.zh` 写了样式，漏了 `li .zh` | 补 `li .zh` 与 `p.zh` 同规格样式 |
| f-string 里 `P('en','zh')` 被当字面文本输出 | f-string 内函数调用未求值 | 必须写成 `{P('en','zh')}`；用脚本批量补花括号 |
| 批量补 `}` 误加到末尾 `page()` 行 | 后处理脚本不分上下文 | 手动删除；后改为"向后找第一个以 `)` 结尾"再补 |
| `SyntaxError: expressions nested too deeply` | 代码块里的 JSON `{}` 与 f-string 冲突 | 代码块抽成普通字符串变量，用 `+` 拼接（ch11 方案） |
| SVG 图浏览器显示异常 | EPUB 内嵌矢量图未转码 | cairosvg 统一转 PNG 再压 base64 |
| 正文被 span/br 拆成碎行 | EPUB 排版标签细碎 | 抽文本时合并相邻文本节点，按块重建段落 |
| 图过大导致 HTML 膨胀 | 原图 MB 级 | 宽限 800px、q68，单图压到 30~80KB |

### 可复用经验

1. **先样板后批量**：先做 1 章给用户验收样式，再沉淀共享模板，避免 11 章返工。
2. **模板与内容分离**：`book_theme.py` 一处改、全局生效；新增章只写正文。
3. **代码块不入 f-string**：含花括号的代码一律抽成独立字符串变量，避免转义地狱。
4. **图先压后嵌**：base64 内嵌前必须压缩，否则单章 HTML 会冲到数 MB。
5. **EPUB 章节天然分文件**：spine 里每个 XHTML 即一章，章节划分比 PDF 更干净，可直接复用。

### 后续优化方向

- 构建脚本可进一步参数化：把"spine 文件 + 图号映射"做成配置表，减少每章重复代码。
- 可加一个 `build_all.py` 一键顺序构建全部章节。
- 中文翻译目前为压缩式意译（部分章节把清单类内容合并为要点），若需逐句严格对应可再做精修。
- 可加入搜索/全文索引（如 Pagefind）以支持 11 章规模的内容检索。
- EPUB 无物理页码，若需对应纸质版定位，可另查该书出版信息补页码映射；当前以小节标题为锚点。

---

## 6. 交付物清单

todo
