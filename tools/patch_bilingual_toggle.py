#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 dist/ 下各中英对照书目录的 HTML 页打上「中文可折叠」补丁。

效果：
  1. 页面加载后**自动隐藏**中文段落（p.zh），正文默认只显示英文；
  2. **点击英文段落**即可显示 / 隐藏紧随其后的中文译文；
  3. 右下角**悬浮按钮**一键在「仅英文 / 仅中文 / 中英对照」三种模式间循环，
     模式写入 localStorage，翻页后保持一致。

范围与取舍：
  * 只处理成对的 p.en / p.zh 段落（同一父节点内紧邻的兄弟节点）；
  * 找不到对应英文段落的 p.zh（例如表格单元格里的中文）会被标记为 .zh-keep
    并**保持常显**，避免正文丢内容；没有对应译文的 p.en 则只是不可点；
  * 标题 / 图注里的 <span class="zh">、<span class="hz"> 等不在范围内，始终中英双语；
  * 没有中英对照段落的页面（纯目录页）自动跳过；
  * 个别页面没有 <main>（如 sa-itv），脚本会退化到 document.body 作为作用域；
  * 所有 CSS 变量都写了回退值（--line/--zh-bg/--accent/--ink），没有这些变量的
    页面也能正常显示。

脚本是幂等的：已打过补丁（存在 id="langbtn"）的文件会跳过。

用法：
  python tools/patch_bilingual_toggle.py --dry-run   # 只检查，不写入
  python tools/patch_bilingual_toggle.py             # dist/*/*.html 全部书籍
  python tools/patch_bilingual_toggle.py --dir dist/llmops_bilingual   # 只处理某一本书
  python tools/patch_bilingual_toggle.py --file dist/sa-itv/Part1.html  # 只处理单文件
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

# ---------- 1. 样式：中文段落默认隐藏 + 悬浮语言按钮 ----------
ANCHOR_STYLE = "</style>"

CSS_BLOCK = """/* ===== 中英对照显示控制：中文段落默认隐藏，点英文段落可展开 ===== */
p.zh{display:none;}
p.zh.zh-keep{display:block;}            /* 没有对应英文段落的译文，保持常显 */
html.lang-both p.zh{display:block;}     /* 中英对照：全部显示 */
html.lang-zh p.zh{display:block;}       /* 仅中文 */
html.lang-zh p.en{display:none;}
p.en.en-paired{cursor:pointer;}
p.en.en-paired:hover{background:#e8f0fe; border-radius:4px; box-shadow:0 0 0 6px #e8f0fe;}
#langbtn{
  position:fixed; right:18px; bottom:18px; z-index:200;
  display:inline-flex; align-items:center; gap:6px;
  background:#fff; border:1px solid var(--line, #e0dbd1); border-radius:999px;
  padding:9px 16px; color:var(--ink, #1f2937); font-family:inherit; font-size:14px;
  line-height:1; white-space:nowrap; cursor:pointer;
  box-shadow:0 2px 8px rgba(15,23,42,.14);
  transition:background .15s ease,color .15s ease,border-color .15s ease;
}
#langbtn:hover{background:var(--zh-bg, #f0f6ff); color:var(--accent, #2563eb); border-color:var(--accent, #2563eb);}
#langbtn .ic{font-size:15px; line-height:1;}
@media (max-width:900px){
  #langbtn{right:12px; bottom:12px; padding:8px 13px; font-size:13px; z-index:80;}
}"""

# ---------- 2. head 里的抢先脚本：首帧前恢复语言模式，避免中文闪现 ----------
ANCHOR_HEAD = "</head>"

HEAD_SCRIPT = """<script>
/* 首帧前恢复上次的语言显示模式，避免中文段落闪现（localStorage 不可用时静默跳过） */
(function(){try{
  var m=localStorage.getItem('seag-lang-mode');
  if(m==='zh'||m==='both'){document.documentElement.classList.add('lang-'+m);}
}catch(e){}})();
</script>"""

# ---------- 3. 悬浮语言切换按钮；插入点优先级：① seag 的 ☰ 按钮之后 ② <body> 之后 ----------
ANCHOR_MENUBTN = '<button id="menubtn" title="目录">☰</button>'
BODY_RE = re.compile(r"<body[^>]*>")

BUTTON_HTML = """
<button id="langbtn" type="button" data-mode="en"
  title="切换语言显示（仅英文 / 仅中文 / 中英对照）" aria-label="切换语言显示"
  ><span class="ic">&#127760;</span><span id="langbtn-txt">仅英文</span></button>"""

# ---------- 4. 尾部脚本：点击段落切换译文 + 悬浮按钮切换模式 ----------
ANCHOR_BODY = "</body>"

TAIL_SCRIPT = """<script>
/* 中英对照控制：
   1) 中文段落（p.zh）默认隐藏，点英文段落（p.en）显示 / 隐藏对应译文；
   2) 右下角悬浮按钮一键在「仅英文 / 仅中文 / 中英对照」之间循环（记入 localStorage）。 */
(function(){
  var KEY='seag-lang-mode';
  var ORDER=['en','zh','both'];
  var MODES={
    en:{cls:'',label:'仅英文',next:'仅中文'},
    zh:{cls:'lang-zh',label:'仅中文',next:'中英对照'},
    both:{cls:'lang-both',label:'中英对照',next:'仅英文'}
  };
  var root=document.documentElement;
  var scope=document.querySelector('main')||document.body;   /* sa-itv 等页面没有 <main> */
  var btn=document.getElementById('langbtn');
  var btnTxt=document.getElementById('langbtn-txt');

  /* 同一父节点内紧邻的 p.en / p.zh 视为一对 */
  function pairOf(en){
    var n=en.nextElementSibling;
    return (n&&n.tagName==='P'&&n.classList.contains('zh'))?n:null;
  }
  function visible(el){
    if(el.style.display){return el.style.display!=='none';}
    return window.getComputedStyle(el).display!=='none';
  }

  if(scope){
    var ens=scope.querySelectorAll('p.en'), i;
    for(i=0;i<ens.length;i++){
      if(pairOf(ens[i])){
        ens[i].classList.add('en-paired');
        ens[i].title='点击显示 / 隐藏中文';
      }
    }
    var zhs=scope.querySelectorAll('p.zh');
    for(i=0;i<zhs.length;i++){
      var prev=zhs[i].previousElementSibling;
      if(!(prev&&prev.tagName==='P'&&prev.classList.contains('en'))){
        zhs[i].classList.add('zh-keep');   /* 没有对应英文段落：常显，避免丢内容 */
      }
    }
    scope.addEventListener('click',function(e){
      var t=e.target;
      if(t&&t.closest&&t.closest('a')){return;}                              /* 正文链接照常跳转 */
      if(window.getSelection&&String(window.getSelection()).length){return;}  /* 划词时不误触 */
      var el=t;
      while(el&&el!==scope){
        if(el.tagName==='P'&&el.classList.contains('en')){break;}
        el=el.parentNode;
      }
      if(!el||el===scope){return;}
      var zh=pairOf(el);
      if(zh){zh.style.display=visible(zh)?'none':'block';}
    });
  }

  function setMode(name,remember){
    var m=MODES[name]||MODES.en;
    root.classList.remove('lang-zh');
    root.classList.remove('lang-both');
    if(m.cls){root.classList.add(m.cls);}
    if(scope){                                 /* 换模式时清掉单段的手动状态，重新同步 */
      var all=scope.querySelectorAll('p.en, p.zh');
      for(var i=0;i<all.length;i++){all[i].style.display='';}
    }
    if(btnTxt){btnTxt.textContent=m.label;}
    if(btn){
      btn.setAttribute('data-mode',name);
      btn.title='当前：'+m.label+'，点击切换为「'+m.next+'」';
      btn.setAttribute('aria-label',btn.title);
    }
    if(remember){try{localStorage.setItem(KEY,name);}catch(e){}}
  }

  var cur='en';
  try{var v=localStorage.getItem(KEY); if(v==='zh'||v==='both'){cur=v;}}catch(e){}
  setMode(cur,false);

  if(btn){
    btn.addEventListener('click',function(){
      var i=ORDER.indexOf(btn.getAttribute('data-mode')||'en');
      if(i<0){i=0;}
      setMode(ORDER[(i+1)%ORDER.length],true);
    });
  }
})();
</script>"""

NEW_BODY = TAIL_SCRIPT + "\n</body>"

DONE_MARK = 'id="langbtn"'


def build_patches(text):
    """按每个文件实际存在的锚点生成替换列表"""
    patches = [
        ("style", ANCHOR_STYLE, CSS_BLOCK + "\n" + ANCHOR_STYLE),
        ("head-script", ANCHOR_HEAD, HEAD_SCRIPT + "\n" + ANCHOR_HEAD),
        ("tail-script", ANCHOR_BODY, TAIL_SCRIPT + "\n" + ANCHOR_BODY),
    ]
    if ANCHOR_MENUBTN in text:                      # software-engineering-at-google
        patches.insert(0, ("lang-button", ANCHOR_MENUBTN, ANCHOR_MENUBTN + BUTTON_HTML))
    else:                                           # 其它书：统一插在 <body> 之后
        m = BODY_RE.search(text)
        if m:
            patches.insert(0, ("lang-button", m.group(0), m.group(0) + BUTTON_HTML))
    return patches


def load(path):
    """读取文件，返回 (文本, 换行符, 是否带 BOM)；文本统一为 \\n"""
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        raw = raw[3:]
    eol = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), eol, bom


def inspect(path):
    """返回 (状态, 说明, patches, 文本, 换行符, BOM)"""
    text, eol, bom = load(path)
    if DONE_MARK in text:
        return "skip", "已打过补丁", [], text, eol, bom
    if '<p class="en">' not in text or '<p class="zh">' not in text:
        return "skip", "本页没有中英对照段落，无需处理", [], text, eol, bom
    patches = build_patches(text)
    if not any(name == "lang-button" for name, _, _ in patches):
        return "error", "找不到按钮插入点（<body> / ☰ 按钮）", [], text, eol, bom
    missing = [name for name, old, _ in patches if old not in text]
    if missing:
        return "error", "未找到片段: " + ", ".join(missing), patches, text, eol, bom
    return "patched", "%d 处替换就绪" % len(patches), patches, text, eol, bom


def patch(path):
    status, note, patches, text, eol, bom = inspect(path)
    if status != "patched":
        return status, note
    for _name, old, new in patches:
        text = text.replace(old, new, 1)
    if eol == "\r\n":
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)
    return "patched", "%d 处替换完成" % len(patches)


def main():
    ap = argparse.ArgumentParser(description="为双语章节页添加「中文默认隐藏 + 点击展开 + 悬浮语言切换」")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不写入")
    ap.add_argument("--dir", default=None, help="只处理该目录下的 *.html（默认：dist/*/*.html）")
    ap.add_argument("--file", default=None, help="只处理指定单文件")
    args = ap.parse_args()

    if args.file:
        pages = [Path(args.file)]
        base = Path(args.file).resolve().parent
    elif args.dir:
        base = Path(args.dir).resolve()
        pages = sorted(base.glob("*.html"))
    else:
        base = DIST
        pages = sorted(DIST.glob("*/*.html"))

    if not pages:
        print("[!] 未找到 html 文件")
        return 1

    print("范围：%s（%d 个文件）\n" % (base, len(pages)))
    stats = {"patched": 0, "skip": 0, "error": 0}
    for page in pages:
        if args.dry_run:
            status, note, _, _, _, _ = inspect(page)
            if status == "patched":
                note = "可打补丁（dry-run 未写入）"
        else:
            status, note = patch(page)
        stats[status] += 1
        flag = {"patched": "OK ", "skip": "-- ", "error": "ERR"}[status]
        try:
            label = str(page.resolve().relative_to(base)).replace("\\", "/")
        except ValueError:
            label = page.name
        print("[%s] %s: %s" % (flag, label, note))

    print("\n合计：修改 %d / 跳过 %d / 失败 %d" % (stats["patched"], stats["skip"], stats["error"]))
    if stats["error"]:
        print("提示：失败的文件结构与预期不符，请按上面的片段名手工检查。")
    return 1 if stats["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
