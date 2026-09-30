/* 回归验证：用最小 DOM 桩在 Node 里真实执行章节页内嵌的「中英对照」脚本，
   检查 ①中文段落加载后默认隐藏 ②点英文段落切换译文 ③悬浮按钮三种模式循环
   ④localStorage 记忆模式并在首帧恢复 ⑤无英文对应的译文不被隐藏。

   用法（在仓库根目录）：
     node tools/verify_bilingual_toggle.js "dist/software-engineering-at-google/*.html"
     node tools/verify_bilingual_toggle.js dist/software-engineering-at-google/chapter-01.html
*/
const fs = require('fs');
const vm = require('vm');

const VOID = new Set(['meta', 'link', 'br', 'img', 'hr', 'input', 'source', 'area', 'base', 'col', 'embed', 'param', 'track', 'wbr']);
const failures = [];

function check(cond, msg) {
  if (cond) { console.log('  ok   ' + msg); } else { failures.push(msg); console.log('  FAIL ' + msg); }
}

function parseAttrs(s) {
  const attrs = {};
  const re = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*("([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(s))) {
    attrs[m[1].toLowerCase()] = m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : (m[5] !== undefined ? m[5] : ''));
  }
  return attrs;
}

function mkEl(tag, attrsStr, parent) {
  const attrs = parseAttrs(attrsStr || '');
  const set = new Set((attrs['class'] || '').split(/\s+/).filter(Boolean));
  const el = { nodeType: 1, tagName: tag.toUpperCase(), childNodes: [], parentNode: parent, attrs, style: {}, listeners: {} };
  el.classList = {
    add() { for (const c of arguments) { set.add(c); } },
    remove() { for (const c of arguments) { set.delete(c); } },
    contains(c) { return set.has(c); },
    toggle(c) { if (set.has(c)) { set.delete(c); } else { set.add(c); } return set.has(c); },
  };
  el.getAttribute = (n) => (n in attrs ? attrs[n] : null);
  el.setAttribute = (n, v) => {
    attrs[n] = String(v);
    if (n === 'class') { set.clear(); String(v).split(/\s+/).filter(Boolean).forEach((c) => set.add(c)); }
  };
  el.addEventListener = (t, f) => { (el.listeners[t] = el.listeners[t] || []).push(f); };
  el.fire = (t, ev) => { (el.listeners[t] || []).forEach((f) => f.call(el, ev)); };
  el.closest = (sel) => {
    let n = el;
    while (n && n.nodeType === 1 && n.tagName !== '#ROOT') {
      if (sel === 'a' && n.tagName === 'A') { return n; }
      n = n.parentNode;
    }
    return null;
  };
  el.querySelectorAll = (sel) => qsa(el, sel);
  el.querySelector = (sel) => qsa(el, sel)[0] || null;
  const sibs = () => (el.parentNode ? el.parentNode.childNodes.filter((n) => n.nodeType === 1) : []);
  Object.defineProperty(el, 'nextElementSibling', { get() { const s = sibs(); const i = s.indexOf(el); return i >= 0 && i + 1 < s.length ? s[i + 1] : null; } });
  Object.defineProperty(el, 'previousElementSibling', { get() { const s = sibs(); const i = s.indexOf(el); return i > 0 ? s[i - 1] : null; } });
  return el;
}

function makeStore(seed) {
  const map = Object.assign({}, seed || {});
  return {
    map,
    getItem: (k) => (k in map ? map[k] : null),
    setItem: (k, v) => { map[k] = String(v); },
    removeItem: (k) => { delete map[k]; },
  };
}

function parse(html) {
  const root = { nodeType: 1, tagName: '#ROOT', childNodes: [], parentNode: null, attrs: {}, style: {}, listeners: {} };
  const stack = [root];
  const re = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[0].startsWith('<!--')) { continue; }
    const tag = m[2].toLowerCase();
    if (m[1] === '/') {
      for (let i = stack.length - 1; i > 0; i--) { if (stack[i].tagName === tag.toUpperCase()) { stack.length = i; break; } }
      continue;
    }
    const parent = stack[stack.length - 1];
    const el = mkEl(tag, m[3], parent);
    parent.childNodes.push(el);
    if (!VOID.has(tag)) { stack.push(el); }
  }
  return root;
}

function matches(el, sel) {
  return sel.split(',').map((s) => s.trim()).some((one) => {
    const parts = one.split('.');
    if (parts[0] && parts[0] !== '*' && el.tagName !== parts[0].toUpperCase()) { return false; }
    return parts.slice(1).every((c) => el.classList.contains(c));
  });
}

function qsa(node, sel, out) {
  out = out || [];
  node.childNodes.forEach((c) => {
    if (c.nodeType !== 1) { return; }
    if (matches(c, sel)) { out.push(c); }
    qsa(c, sel, out);
  });
  return out;
}

function boot(html, store) {
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const stripped = html
    .replace(/<script>[\s\S]*?<\/script>/gi, '<script></script>')
    .replace(/<style>[\s\S]*?<\/style>/gi, '<style></style>')
    .replace(/<pre>[\s\S]*?<\/pre>/gi, '<pre></pre>')
    .replace(/<code>[\s\S]*?<\/code>/gi, '<code></code>');
  const root = parse(stripped);
  const htmlEl = qsa(root, 'html')[0];
  const headScript = scripts.find((s) => s.includes("m==='zh'||m==='both'"));
  const tailScript = scripts.find((s) => s.includes('MODES={'));

  // 近似本页 CSS 层叠：inline style 优先，其次 html.lang-* 与 p.zh 默认隐藏
  function computedDisplay(el) {
    if (el.style.display) { return el.style.display; }
    if (el.tagName === 'P') {
      if (el.classList.contains('en')) { return htmlEl.classList.contains('lang-zh') ? 'none' : 'block'; }
      if (el.classList.contains('zh')) {
        if (el.classList.contains('zh-keep')) { return 'block'; }
        return (htmlEl.classList.contains('lang-zh') || htmlEl.classList.contains('lang-both')) ? 'block' : 'none';
      }
    }
    return 'block';
  }
  const document = {
    documentElement: htmlEl,
    body: qsa(root, 'body')[0] || root,
    querySelector: (s) => qsa(root, s)[0] || null,
    querySelectorAll: (s) => qsa(root, s),
    getElementById: (id) => qsa(root, '*').find((e) => e.attrs.id === id) || null,
  };
  const win = { getComputedStyle: (el) => ({ display: computedDisplay(el) }), getSelection: () => '' };
  const ctx = { document, window: win, localStorage: store, getComputedStyle: win.getComputedStyle };
  vm.createContext(ctx);
  vm.runInContext(headScript, ctx);            // head 抢先脚本（首帧恢复模式）
  return { ctx, headScript, tailScript, root, document, win, htmlEl, computedDisplay };
}

function run(file) {
  const html = fs.readFileSync(file, 'utf8');
  const store = makeStore();
  const b0 = boot(html, store);
  if (!b0.headScript || !b0.tailScript) {
    // 纯目录页没有中英对照段落，补丁工具会主动跳过 —— 记为「不适用」而非失败
    if (!/<p class="en">/.test(html) && !/<p class="zh">/.test(html)) {
      console.log('\n=== %s ===\n  SKIP 纯目录页（无中英对照段落，补丁工具已跳过）', file);
      return;
    }
    console.log('\n=== %s ===\n  FAIL 未找到补丁脚本（head/tail）', file);
    failures.push(file + ' 未找到补丁脚本');
    return;
  }
  vm.runInContext(b0.tailScript, b0.ctx);   // 尾部脚本

  const isShown = (b, el) => (el.style.display ? el.style.display !== 'none' : b.computedDisplay(el) !== 'none');
  // sa-itv 等页面没有 <main>，脚本会退化到 body，测试也要一致地作用于同一容器
  const main = b0.document.querySelector('main') || b0.document.body;
  const inMainEn = qsa(main, 'p.en');
  const inMainZh = qsa(main, 'p.zh');
  const paired = inMainEn.filter((e) => e.nextElementSibling && e.nextElementSibling.classList.contains('zh'));
  const orphanZh = inMainZh.filter((e) => !(e.previousElementSibling && e.previousElementSibling.classList.contains('en')));
  const btn = b0.document.getElementById('langbtn');
  const btnTxt = b0.document.getElementById('langbtn-txt');

  console.log('\n=== %s ===', file);
  console.log('  p.en=%d  p.zh=%d  成对=%d  孤立译文=%d', inMainEn.length, inMainZh.length, paired.length, orphanZh.length);

  console.log('[1] 加载后自动隐藏中文');
  check(paired.length > 0, '存在成对的 p.en / p.zh');
  check(paired.every((e) => !isShown(b0, e.nextElementSibling)), '全部成对译文默认隐藏');
  check(orphanZh.every((e) => isShown(b0, e)), '无英文对应的译文保持可见（不丢内容）');
  check(inMainEn.filter((e) => e.classList.contains('en-paired')).length === paired.length, '可点击英文段落数 == 成对译文数');
  check(inMainEn.every((e) => isShown(b0, e)), '英文段落全部可见');

  console.log('[2] 点击英文段落显示 / 隐藏中文');
  const en0 = paired[0];
  const zh0 = en0.nextElementSibling;
  main.fire('click', { target: en0 });
  check(isShown(b0, zh0), '点一下：译文显示');
  main.fire('click', { target: en0 });
  check(!isShown(b0, zh0), '再点一下：译文重新隐藏');

  console.log('[3] 悬浮按钮循环切换 仅英文 / 仅中文 / 中英对照');
  check(!!btn && !!btnTxt, '按钮与文案节点存在');
  check(btnTxt.textContent === '仅英文', '初始文案「仅英文」');
  check(!b0.htmlEl.classList.contains('lang-zh') && !b0.htmlEl.classList.contains('lang-both'), '初始 html 无 lang-* 类');
  check(/点击显示 \/ 隐藏中文/.test(en0.title || (en0.attrs.title || '')), '英文段落带操作提示 title');

  btn.fire('click');
  check(btnTxt.textContent === '仅中文' && b0.htmlEl.classList.contains('lang-zh'), '切到「仅中文」');
  check(inMainEn.every((e) => !isShown(b0, e)), '仅中文：英文段落全部隐藏');
  check(inMainZh.every((e) => isShown(b0, e)), '仅中文：中文段落全部显示');
  check(store.map['seag-lang-mode'] === 'zh', '模式写入 localStorage');

  btn.fire('click');
  check(btnTxt.textContent === '中英对照' && b0.htmlEl.classList.contains('lang-both'), '切到「中英对照」');
  check(!b0.htmlEl.classList.contains('lang-zh'), '切换后旧类已移除');
  check(inMainEn.every((e) => isShown(b0, e)) && inMainZh.every((e) => isShown(b0, e)), '中英对照：两种语言都显示');

  console.log('[4] 中英对照模式下仍可单段收起');
  main.fire('click', { target: en0 });
  check(!isShown(b0, zh0), '单段点击可隐藏该段译文');
  main.fire('click', { target: en0 });
  check(isShown(b0, zh0), '再点恢复显示');
  if (paired.length >= 2) {
    const p2 = paired[1];
    const z2 = p2.nextElementSibling;
    check(!!z2 && isShown(b0, z2), '邻段不受影响');
  } else {
    console.log('  ok   （本页只有 %d 组对照，跳过邻段检查）', paired.length);
  }

  btn.fire('click');
  check(btnTxt.textContent === '仅英文', '循环回「仅英文」');
  check(!b0.htmlEl.classList.contains('lang-zh') && !b0.htmlEl.classList.contains('lang-both'), 'html 类已还原');
  check(paired.every((e) => !isShown(b0, e.nextElementSibling)), '仅英文：中文重新隐藏');
  check(store.map['seag-lang-mode'] === 'en', 'localStorage 记录 en');

  console.log('[5] 刷新后恢复上次模式（head 抢先脚本首帧生效，不闪中文）');
  const b1 = boot(html, makeStore({ 'seag-lang-mode': 'both' }));
  check(b1.htmlEl.classList.contains('lang-both'), 'head 脚本加 lang-both');
  const m1 = b1.document.querySelector('main') || b1.document.body;
  check(qsa(m1, 'p.zh').every((e) => b1.computedDisplay(e) === 'block'), '首帧中文即可见');
  check(qsa(m1, 'p.en').every((e) => b1.computedDisplay(e) === 'block'), '首帧英文仍可见');
  vm.runInContext(b1.tailScript, b1.ctx);
  check(b1.document.getElementById('langbtn-txt').textContent === '中英对照', '刷新后按钮文案 == 模式');
  check(b1.document.getElementById('langbtn').attrs['data-mode'] === 'both', 'data-mode 同步为 both');

  const b2 = boot(html, makeStore({ 'seag-lang-mode': 'zh' }));
  check(b2.htmlEl.classList.contains('lang-zh'), 'head 脚本恢复「仅中文」');
  const m2 = b2.document.querySelector('main') || b2.document.body;
  check(qsa(m2, 'p.en').every((e) => b2.computedDisplay(e) === 'none'), '首帧英文即隐藏');
  vm.runInContext(b2.tailScript, b2.ctx);
  check(b2.document.getElementById('langbtn-txt').textContent === '仅中文', '刷新后按钮文案「仅中文」');
  b2.document.getElementById('langbtn').fire('click');
  check(b2.document.getElementById('langbtn-txt').textContent === '中英对照', '「仅中文」点一下 → 「中英对照」');

  console.log('[6] 段落内元素 / 链接的点击行为');
  const b3 = boot(html, makeStore());
  vm.runInContext(b3.tailScript, b3.ctx);
  const m3 = b3.document.querySelector('main') || b3.document.body;
  const p3 = qsa(m3, 'p.en').filter((e) => e.nextElementSibling && e.nextElementSibling.classList.contains('zh'))[0];
  m3.fire('click', { target: mkEl('a', 'href="#"', p3) });
  check(!isShown(b3, p3.nextElementSibling), '点段落里的链接不误触');
  m3.fire('click', { target: mkEl('code', '', p3) });
  check(isShown(b3, p3.nextElementSibling), '点段落里的 <code> 仍能展开译文');
}

function expand(args) {
  const out = [];
  // 按 '/' 切段：'*' 段做通配，其余段为真实目录名
  const walk = (dir, segs) => {
    if (!segs.length) { return; }
    const seg = segs[0];
    const last = segs.length === 1;
    let entries;
    try { entries = fs.readdirSync(dir || '.', { withFileTypes: true }); } catch (e) { return; }
    for (const e of entries) {
      const p = (dir ? dir + '/' : '') + e.name;
      if (seg.includes('*')) {
        const re = new RegExp('^' + seg.split('*').map((s) =>
          s.replace(/[.+^${}()|[\]\\]/g, '\\$&')).join('.*') + '$');
        if (!re.test(e.name)) { continue; }
      } else if (e.name !== seg) { continue; }
      if (last) { if (e.isFile()) { out.push(p); } }
      else if (e.isDirectory()) { walk(p, segs.slice(1)); }
    }
  };
  args.forEach((a) => {
    if (a.includes('*')) { walk('', a.replace(/\\/g, '/').replace(/\/+$/, '').split('/')); }
    else { out.push(a); }
  });
  return out.sort();
}

const files = expand(process.argv.slice(2));
files.forEach(run);
console.log('\n===== %s =====', failures.length ? '有 ' + failures.length + ' 项失败' : '全部通过');
process.exit(failures.length ? 1 : 0);


