# -*- coding: utf-8 -*-
"""
Shared theme module for the "Software Engineering at Google" bilingual HTML reader.
All chapter/build scripts import from here so that all 28 files share one look.

Helpers:
  esc(s)                 -> html-escape a plain string
  P(en, zh)              -> <p class=en>EN</p><p class=zh>ZH</p>
  LI(en, zh)             -> <li><p class=en>..</p><p class=zh>..</p></li>
  CODE(txt)              -> <pre><code>..</code></pre>  (English code, escaped, NOT translated)
  pagemark(n)            -> <div class=pagemark>原书第 N 页</div>
  figure(img_uri, cap_en, cap_zh) -> <figure>... with bilingual figcaption
  fig_placeholder(note_en, note_zh) -> decorative-cartoon placeholder block "[图示]"
  img_to_data_uri(pil_img) -> base64 JPEG data URI (width<=800, RGB, q=68)
  row(cells_en, cells_zh, header=False)
  page(...)              -> assemble a complete standalone HTML document and write it
"""
import os, base64, io, html
from PIL import Image

# ---------------------------------------------------------------------------
# Book-level navigation table (filename -> (en title, zh title))
# ---------------------------------------------------------------------------
BOOK = [
    ("front-matter.html", "Front Matter", "前置：封面 / 版权 / 目录 / 序 / 前言"),
    ("chapter-01.html", "What Is Software Engineering?", "什么是软件工程？"),
    ("chapter-02.html", "How to Work Well on Teams", "如何在团队中高效协作"),
    ("chapter-03.html", "Knowledge Sharing", "知识共享"),
    ("chapter-04.html", "Engineering for Equity", "面向公平的工程"),
    ("chapter-05.html", "How to Lead a Team", "如何领导团队"),
    ("chapter-06.html", "Leading at Scale", "规模化领导"),
    ("chapter-07.html", "Measuring Engineering Productivity", "度量工程生产力"),
    ("chapter-08.html", "Style Guides and Rules", "风格指南与规则"),
    ("chapter-09.html", "Code Review", "代码评审"),
    ("chapter-10.html", "Documentation", "文档"),
    ("chapter-11.html", "Testing Overview", "测试概览"),
    ("chapter-12.html", "Unit Testing", "单元测试"),
    ("chapter-13.html", "Test Doubles", "测试替身"),
    ("chapter-14.html", "Larger Testing", "大规模测试"),
    ("chapter-15.html", "Deprecation", "弃用"),
    ("chapter-16.html", "Version Control and Branch Management", "版本控制与分支管理"),
    ("chapter-17.html", "Code Search", "代码搜索"),
    ("chapter-18.html", "Build Systems and Build Philosophy", "构建系统与构建理念"),
    ("chapter-19.html", "Critique: Google's Code Review Tool", "评述：Google 的代码评审工具"),
    ("chapter-20.html", "Static Analysis", "静态分析"),
    ("chapter-21.html", "Dependency Management", "依赖管理"),
    ("chapter-22.html", "Large-Scale Changes", "大规模变更"),
    ("chapter-23.html", "Continuous Integration", "持续集成"),
    ("chapter-24.html", "Continuous Delivery", "持续交付"),
    ("chapter-25.html", "Compute as a Service", "计算即服务"),
    ("closing.html", "Afterword & About the Authors", "结语与作者简介"),
]


def esc(s):
    if s is None:
        return ""
    return html.escape(str(s), quote=False)


def P(en, zh=""):
    s = '<p class="en">' + esc(en) + '</p>\n'
    if zh:
        s += '<p class="zh">' + esc(zh) + '</p>\n'
    return s


def H(level, en, zh="", anchor=None):
    a = (' id="%s"' % anchor) if anchor else ""
    s = '<h%d%s>' % (level, a) + esc(en)
    if zh:
        s += ' <span class="hz">· ' + esc(zh) + '</span>'
    s += '</h%d>\n' % level
    return s


def LI(en, zh=""):
    s = '<li>\n<p class="en">' + esc(en) + '</p>\n'
    if zh:
        s += '<p class="zh">' + esc(zh) + '</p>\n'
    s += '</li>\n'
    return s


def UL(items):
    """items: list of (en, zh) tuples"""
    return "<ul>\n" + "".join(LI(e, z) for e, z in items) + "</ul>\n"


def CODE(txt):
    return '<pre><code>' + esc(txt).replace("\n", "\n") + '</code></pre>\n'


def pagemark(n):
    return '<div class="pagemark">原书第 %s 页</div>\n' % n


def fig_placeholder(note_en="", note_zh=""):
    s = '<div class="figph"><span class="badge">[图示]</span>\n'
    s += '<p class="en">' + esc(note_en) + '</p>\n'
    if note_zh:
        s += '<p class="zh">' + esc(note_zh) + '</p>\n'
    s += '</div>\n'
    return s


def figure(img_uri, cap_en="", cap_zh=""):
    if not img_uri:
        return fig_placeholder(cap_en, cap_zh)
    s = '<figure><img src="%s" alt="%s">\n' % (img_uri, esc(cap_en))
    if cap_en or cap_zh:
        s += '<figcaption>'
        if cap_en:
            s += '<span class="en">' + esc(cap_en) + '</span> '
        if cap_zh:
            s += '<span class="zh">' + esc(cap_zh) + '</span>'
        s += '</figcaption>\n'
    s += '</figure>\n'
    return s


def img_to_data_uri(pil_img, max_w=800, quality=68):
    """Open / normalize a PIL image -> JPEG base64 data URI. RGBA/P/CMYK -> RGB;
    jpx (JPEG2000) decoded by PIL is normalized here too."""
    im = pil_img
    if im.mode in ("RGBA", "P", "LA", "CMYK", "1"):
        im = im.convert("RGB")
    if im.width > max_w:
        h = int(im.height * max_w / im.width)
        im = im.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + b64


def table(header_rows, body_rows):
    """header_rows / body_rows: list of rows; each row is list of (en, zh) tuples."""
    out = '<table>\n'
    for i, row in enumerate(header_rows or []):
        out += "<thead><tr>"
        for en, zh in row:
            out += "<th>" + esc(en)
            if zh:
                out += ' <span class="zh">' + esc(zh) + "</span>"
            out += "</th>"
        out += "</tr></thead>\n"
    out += "<tbody>\n"
    for row in body_rows:
        out += "<tr>"
        for en, zh in row:
            out += "<td>" + esc(en)
            if zh:
                out += '<p class="zh">' + esc(zh) + "</p>"
            out += "</td>"
        out += "</tr>\n"
    out += "</tbody></table>\n"
    return out


# ---------------------------------------------------------------------------
# CSS / JS (plain strings, NOT f-strings, so braces are safe)
# ---------------------------------------------------------------------------
CSS = """
:root{
  --accent:#2563eb; --zh-bg:#f0f6ff; --zh-border:#2563eb;
  --code-bg:#0f172a; --code-ink:#e2e8f0; --ink:#1f2937;
  --page-bg:#faf6f1; --line:#e7e2d9;
}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{
  margin:0; background:var(--page-bg); color:var(--ink);
  font-size:18px; line-height:1.7;
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei","Segoe UI",sans-serif;
}
a{color:var(--accent); text-decoration:none;}
a:hover{text-decoration:underline;}
#menubtn{
  position:fixed; top:14px; right:18px; z-index:200;
  background:#fff; border:1px solid var(--line); border-radius:8px;
  padding:8px 12px; font-size:18px; cursor:pointer; color:var(--ink);
  box-shadow:0 1px 3px rgba(0,0,0,.08);
}
#toc{
  position:fixed; top:0; left:0; width:300px; height:100vh; overflow-y:auto;
  background:#ffffff; border-right:1px solid var(--line);
  padding:26px 18px 60px; transition:transform .25s ease; z-index:150;
}
body.toc-hidden #toc{transform:translateX(-300px);}
#toc h2{font-size:15px; color:#6b7280; margin:0 0 10px; letter-spacing:.5px;}
#toc .brand{font-size:17px; font-weight:700; color:var(--ink); margin-bottom:4px; line-height:1.4;}
#toc .brand small{display:block; font-weight:400; color:#9ca3af; font-size:12px;}
#toc ol{list-style:none; margin:14px 0 0; padding:0; counter-reset:ch;}
#toc ol li{margin:0;}
#toc a.cl{
  display:block; padding:5px 8px; border-radius:6px; color:#374151;
  font-size:14px; line-height:1.35;
}
#toc a.cl .n{color:#9ca3af; margin-right:6px; font-variant-numeric:tabular-nums;}
#toc a.cl.active{background:var(--zh-bg); color:var(--accent); font-weight:700;}
#toc a.cl:hover{background:#f3f4f6; text-decoration:none;}
#toc .sub{list-style:none; margin:2px 0 6px; padding:0 0 0 20px;}
#toc .sub a{display:block; padding:3px 8px; font-size:13px; color:#6b7280; border-radius:6px;}
#toc .sub a:hover{background:#f3f4f6; text-decoration:none;}
main{
  margin-left:300px; max-width:900px; margin-right:auto;
  padding:46px 52px 80px; transition:margin-left .25s ease;
}
body.toc-hidden main{margin-left:0;}
h1{font-size:30px; line-height:1.3; margin:0 0 6px; color:#111827;}
h1 .hz{display:block; font-size:20px; color:var(--accent); font-weight:600; margin-top:6px;}
h2{font-size:23px; margin:38px 0 10px; color:#111827; scroll-margin-top:20px; border-left:4px solid var(--accent); padding-left:12px;}
h2 .hz{color:var(--accent); font-size:18px; font-weight:600;}
h3{font-size:19px; margin:26px 0 6px; color:#1f2937; scroll-margin-top:20px;}
h3 .hz{color:var(--accent); font-size:16px; font-weight:600;}
p.en{margin:16px 0 0;}
p.zh{
  background:var(--zh-bg); border-left:4px solid var(--zh-border);
  border-radius:0 8px 8px 0; padding:10px 16px; margin:6px 0 18px; color:#1e3a8a;
}
li p.en{margin:6px 0 0;}
li p.zh{
  background:var(--zh-bg); border-left:4px solid var(--zh-border);
  border-radius:0 8px 8px 0; padding:8px 14px; margin:4px 0 10px; color:#1e3a8a;
}
ul,ol{padding-left:26px;}
code{
  font-family:"SF Mono",Menlo,Consolas,monospace; font-size:.92em;
  background:#fce7f3; color:#be185d; padding:2px 6px; border-radius:4px;
}
pre{
  background:var(--code-bg); color:var(--code-ink);
  padding:14px 16px; border-radius:8px; overflow-x:auto;
  font-family:"SF Mono",Menlo,Consolas,monospace; font-size:13.5px; line-height:1.55;
}
pre code{background:none; color:inherit; padding:0; font-size:inherit;}
table{border-collapse:collapse; width:100%; margin:18px 0; font-size:15px; background:#fff;}
th,td{border:1px solid #cbd5e1; padding:8px 10px; vertical-align:top; text-align:left;}
th{background:#f1f5f9;}
td p.zh{margin:6px 0 0; padding:4px 8px; font-size:14px;}
figure{margin:22px 0; text-align:center;}
figure img{max-width:100%; border:1px solid #e5e7eb; border-radius:8px;}
figcaption{font-size:13.5px; color:#6b7280; margin-top:8px; text-align:left;}
figcaption .zh{display:block; color:#1e3a8a; margin-top:2px;}
.figph{
  border:1px dashed #c7d2fe; background:#f8fafc; border-radius:8px;
  padding:12px 16px; margin:18px 0;
}
.figph .badge{display:inline-block; background:var(--accent); color:#fff; border-radius:4px; padding:1px 8px; font-size:12px; margin-bottom:4px;}
.figph p.en{margin-top:4px; color:#6b7280; font-size:14px;}
.pagemark{
  text-align:center; color:#9ca3af; font-size:12px; letter-spacing:1px;
  margin:26px 0; padding-top:10px; border-top:1px dashed #e5e7eb;
}
.pager{display:flex; gap:10px; margin-top:56px;}
.pager a{
  flex:1; text-align:center; padding:11px 8px; border:1px solid #d1d5db;
  border-radius:8px; background:#fff; color:var(--ink); font-size:14px; line-height:1.4;
}
.pager a:hover{background:var(--accent); color:#fff; text-decoration:none; border-color:var(--accent);}
.pager a.disabled{opacity:.4; pointer-events:none;}
.pager a .t{display:block; font-weight:600;}
.pager a .d{display:block; font-size:12px; color:#9ca3af; margin-top:2px;}
.pager a:hover .d{color:#dbeafe;}
@media (max-width:900px){
  #toc{transform:translateX(-300px); box-shadow:2px 0 16px rgba(0,0,0,.18);}
  body.toc-open #toc{transform:translateX(0);}
  main{margin-left:0; padding:60px 18px 80px;}
}
"""

JS = """
(function(){
  var btn=document.getElementById('menubtn');
  var mq=window.matchMedia('(max-width:900px)');
  function apply(){
    if(mq.matches){
      document.body.classList.remove('toc-hidden');
    }else{
      document.body.classList.remove('toc-open');
    }
  }
  btn.addEventListener('click',function(){
    if(mq.matches){document.body.classList.toggle('toc-open');}
    else{document.body.classList.toggle('toc-hidden');}
  });
  // close drawer after clicking a section link on narrow screens
  document.querySelectorAll('#toc a').forEach(function(a){
    a.addEventListener('click',function(){ if(mq.matches){document.body.classList.remove('toc-open');} });
  });
  apply();
  mq.addEventListener('change',apply);
})();
"""


def _toc_html(active, sections):
    """Build the left TOC. `active` = filename of current page.
    `sections` = list of (anchor_id, zh_title) for in-chapter anchors."""
    h = '<h2>Contents · 目录</h2>\n'
    h += '<div class="brand">Software Engineering at Google<small>软件开发的工程之道 · 中英对照</small></div>\n'
    h += '<ol>\n'
    for fn, en, zh in BOOK:
        label = zh + (' <span style="color:#9ca3af;font-weight:400">' + esc(en) + '</span>')
        cls = ' class="active"' if fn == active else ''
        h += '  <li><a class="cl"%s href="%s">%s</a>\n' % (cls, fn, label)
        if fn == active and sections:
            h += '    <ul class="sub">\n'
            for anc, tz in sections:
                h += '      <li><a href="#%s">%s</a></li>\n' % (anc, esc(tz))
            h += '    </ul>\n'
        h += '  </li>\n'
    h += '</ol>\n'
    return h


def page(out_dir, filename, title_en, title_zh, body, prev=None, next_=None, sections=None):
    """
    Write a standalone HTML file.
      prev / next_ : (href, label_en, label_zh) or None  (None -> disabled)
      sections: list of (anchor_id, zh_title)
    Returns the absolute path and size in KB.
    """
    nav = _toc_html(filename, sections or [])

    def pager_btn(triple, direction):
        if triple is None:
            href = "#"
            label_en = "Prev" if direction == "prev" else "Next"
            label_zh = "上一章" if direction == "prev" else "下一章"
            return '<a class="disabled"><span class="t">%s</span><span class="d">%s</span></a>' % (label_zh, label_en)
        href, len_, lzh = triple
        mid = direction == "mid"
        t = "返回目录" if mid else lzh
        d = "Index" if mid else len_
        return '<a href="%s"><span class="t">%s</span><span class="d">%s</span></a>' % (href, t, d)

    pager = '<nav class="pager">' + pager_btn(prev, "prev") \
            + pager_btn(("index.html", "Index", "返回目录"), "mid") \
            + pager_btn(next_, "next") + '</nav>\n'

    h = []
    h.append('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">')
    h.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    h.append('<title>' + esc(title_en) + ' · ' + esc(title_zh) + '</title>')
    h.append('<style>' + CSS + '</style>\n</head>\n<body>')
    h.append('<button id="menubtn" title="目录">☰</button>')
    h.append('<nav id="toc">\n' + nav + '</nav>\n')
    h.append('<main>')
    h.append('<h1>' + esc(title_en) + '<span class="hz">' + esc(title_zh) + '</span></h1>')
    h.append(body)
    h.append(pager)
    h.append('</main>\n<script>' + JS + '</script>\n</body>\n</html>')

    doc = "".join(h)
    out_path = os.path.join(out_dir, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    kb = os.path.getsize(out_path) / 1024.0
    print("[book_theme] wrote %s  (%.1f KB)" % (filename, kb))
    return out_path, kb
