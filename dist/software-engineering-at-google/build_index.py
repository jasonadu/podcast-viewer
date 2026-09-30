# -*- coding: utf-8 -*-
import book_theme as bt

OUT = "/home/user/Doubao/chats/38443910031250178/sse-book"

body = ""
body += bt.P(
    "This is a self-contained, bilingual (English / Chinese) reading edition of "
    "<i>Software Engineering at Google: Lessons Learned from Programming Over Time</i> "
    "(Titus Winters, Tom Manshreck, Hyrum Wright, O'Reilly). Each English paragraph is "
    "followed by its Chinese translation, code is kept in English, and figures are "
    "embedded inline so every page can be opened offline by double-clicking the file.",
    "这是《Software Engineering at Google：软件工程之道》（Titus Winters、Tom Manshreck、"
    "Hyrum Wright 著，O'Reilly）的中英对照自包含阅读版。每段英文原文下方紧跟中文译文，"
    "代码块保留英文，图片以 base64 内嵌——双击任意 HTML 文件即可离线阅读，不依赖外部资源。"
)
body += bt.pagemark("目录")
body += '<h2>开始阅读 <span class="hz">Start Reading</span></h2>\n'
body += '<ul>\n'
for fn, en, zh in bt.BOOK:
    body += '  <li><a href="%s">%s</a> <span style="color:#9ca3af;font-size:14px">· %s</span></li>\n' % (fn, zh, en)
body += '</ul>\n'

bt.page(
    OUT, "index.html",
    "Software Engineering at Google — Bilingual Reader",
    "软件工程之道 · 中英对照阅读版",
    body, prev=None, next_=("front-matter.html", "Front Matter", "前置"),
)
