#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SE Radio 节目浏览器构建脚本

流程：读取 data/se_radio_23379.json
  → 去重(mp3 相同取最新) / 期数修正(标题编号优先) / 描述清洗(白名单标签、修正链接、去赞助尾注、标注截断)
  → 10 类规则分类 + 人工覆盖表(OVERRIDES)
  → 节目数据写入 dist/se_radio_data.json（HTML 使用时动态加载，不内嵌）
  → 渲染 tools/viewer_template.html 占位符 → 输出 dist/se_radio_viewer.html
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from html import escape as h_escape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "se_radio_23379.json"
TPL = ROOT / "tools" / "viewer_template.html"
OUT = ROOT / "dist" / "se_radio_viewer.html"
DATA_OUT = ROOT / "dist" / "se_radio_data.json"
SUBTITLES_DIR = ROOT / "subtitles"

DEFAULT_CAT = "practice"

# ---------- SRT 字幕解析与匹配 ----------

def parse_srt(text):
    """解析 SRT 文本，返回 cues 列表 [{start, end, text}]"""
    lines = text.splitlines()
    cues = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        # 序号行
        try:
            int(lines[i].strip())
        except ValueError:
            i += 1
            continue
        i += 1
        if i >= len(lines):
            break
        # 时间行
        m = re.match(r'(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})', lines[i])
        if not m:
            i += 1
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        i += 1
        # 文本行
        txt = []
        while i < len(lines) and lines[i].strip():
            txt.append(re.sub(r'<[^>]+>', '', lines[i]).strip())
            i += 1
        t = ' '.join(txt).strip()
        if t:
            cues.append({"s": start, "e": end, "t": t})
    return cues


def build_subtitle_index():
    """扫描字幕目录，返回 {normalized_name: srt 文件名} 匹配键映射。

    仅记录"哪一集有哪个字幕文件"（文件名），不内嵌字幕内容；
    字幕在播放器打开时由前端 fetch srt 并在浏览器端解析。
    """
    index = {}
    if not SUBTITLES_DIR.is_dir():
        return index
    for f in SUBTITLES_DIR.glob("*.srt"):
        try:
            text = f.read_text(encoding="utf-8")
            cues = parse_srt(text)
            if not cues:
                continue
            stem = f.stem  # e.g. "600_william_morgan_..."
            index[stem.lower()] = f.name
            # 也存储纯数字前缀 (如 "600")
            num_prefix = re.match(r'^(\d+)_', stem)
            if num_prefix:
                index[num_prefix.group(1)] = f.name
        except Exception as e:
            print(f"  跳过字幕 {f.name}: {e}")
    return index

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CATEGORIES = [
    {"id": "ai-ml",        "zh": "AI 与机器学习",      "en": "AI & Machine Learning",        "hue": 258},
    {"id": "ai-agents",    "zh": "AI 智能体与辅助开发", "en": "AI Agents & AI-Assisted Dev",  "hue": 285},
    {"id": "security",     "zh": "安全与隐私",         "en": "Security & Privacy",           "hue": 0},
    {"id": "languages",    "zh": "编程语言与运行时",    "en": "Languages & Runtimes",         "hue": 205},
    {"id": "data",         "zh": "数据库与数据工程",    "en": "Databases & Data",             "hue": 168},
    {"id": "cloud",        "zh": "云与基础设施",       "en": "Cloud & Infrastructure",       "hue": 215},
    {"id": "architecture", "zh": "软件架构与设计",      "en": "Architecture & Design",        "hue": 32},
    {"id": "testing",      "zh": "测试与质量",         "en": "Testing & Quality",            "hue": 88},
    {"id": "devops",       "zh": "DevOps 与可观测性",   "en": "DevOps & Observability",       "hue": 240},
    {"id": "practice",     "zh": "工程实践与职业发展",  "en": "Practice & Career",            "hue": 342},
]
CAT_IDS = {c["id"] for c in CATEGORIES}

# 分类规则：按优先级排列；标题先匹配；标题未命中再匹配描述前 4000 字符。
# 顺序 = 优先级：安全 > AI 智能体 > 测试 > 可观测性 > 数据 > 云 > Web/UI > AI/ML > 语言 > 架构 > DevOps。
RULES = [
    ("security",
     r"prompt.?injection|\bowasp\b|sigstore|backdoor|threat.?model|zero.?trust|vulnerab|\binject|\bcryptograph|\bpki\b|oauth|\bauthent|\bauthoriz|\bidentity|\bransom|\bmalware|\bphish|\bexploit|\battacker|\battack|secur|insec|privacy|credential|\bsecret|\bleak|cyber|pentest|penetr|\bpolic"),
    ("ai-agents",
     r"\bagents?\b|copilot|coding assistant|model context protocol|\bmcp\b|agentic|harness for ai|spec.?driven|ai.?assisted"),
    ("testing",
     r"\btest|\btesting|\bmutation|\bfuzz|\bflaky|\bkata\b|property.based|approval test|quality assurance|code review|code health|software quality|\bdebug|program repair|\bbugs?\b"),
    ("devops",
     r"observab|\btelemetry|monitor|alerting"),
    ("data",
     r"database|postgres|mysql|redis|data lake|data engineer|data governance|data observability|data infrastructure|dataflow|dataware|\bsql\b|vector database|timescale|timeseries|time series|iceberg|\bdruid|\bkudu|materialize|mvcc|b\+tree|b-tree|ksqldb|vitess|faunadb|cockroachdb|\bdbos\b|supabase|dynamodb|polars|pandas|dataframe|stream processing|streaming database|web scraping|\betl\b|geospatial|\bgis\b|overture|feature store|analytics"),
    ("cloud",
     r"kubernetes|\bk8s\b|cloud|\baws\b|serverless|infrastructure|terraform|opentofu|\bpaas\b|\bvpc\b|\bebpf\b|\bbgp\b|\bdns\b|network|service mesh|microvm|data center|datacenter|linux|bare metal|finops|docker|fly\.io|cloudflare|multi.?cloud|\bvpn\b|azure"),
    ("languages",
     r"htmx|\breact|svelte|next\.js|blazor|flutter|android|\bios\b|\bgui\b|\btui\b|\bcli\b|command.line|terminal|browser extension|browser|\bmicro frontend|front.?end|\belm\b|phoenix|liveview|user interface|usability|leaflet|cross.?platform|\bui\b|no.?code|\bmobile app\b"),
    ("practice",
     r"accessib|\bux\b|\busability\b|dark pattern"),
    ("ai-ml",
     r"\bai\b|\ba\.i\.|llm|genai|gen ai|generative|machine learning|deep learning|neural|\bnlp\b|\brag\b|language model|foundational model|tinyml|chatbot|voice|speech|data science|diffusion|transformer|small language model|retrieval.augmented|openai|fastai|\bml\b|mlops|\bprompt|wolfram|mathematica|conversational"),
    ("languages",
     r"\brust\b|\bpython\b|\bgo\b|golang|\bjava\b|kotlin|scala|elixir|ocaml|dart|typescript|javascript|\bjs\b|\bruby\b|perl|crystal|\belm\b|\bc\+\+|modern c|\bc\b|\blisp\b|smalltalk|type system|type check|compiler|interpreter|runtime|programming language|erlang|haskell|tidyverse|functional programming|spring|curl|fastapi|\.net\b"),
    ("architecture",
     r"architect|microservice|\bsoa\b|patterns?\b|coupling|event sourcing|event.driven|event model|event storming|\bddd\b|domain.driven|monolith|distributed|consensus|refactor|legacy|moderniz|\bdesign|integration|messaging|\bnats\b|kafka|message bus|graphql|grpc|openapi|\bapi\b|protocol|configur|workflow|twelve.factor|\bqueue\b|local.first|\bw3c\b|webrtc|web standards"),
    ("devops",
     r"devops|ci/?cd|gitops|\bsre\b|incident|platform engineer|developer platform|error tracking|trunk.based|delivery|deployment|deploy|release|pipeline|helm|service level|error budget"),
]

# 人工覆盖（按原始 index 修正分类）
OVERRIDES = {
    0: "practice",       # About：站点介绍
    5: "architecture",   # MDSD Pt.1（模型驱动开发）
    6: "architecture",   # MDSD Pt.2
    9: "architecture",   # Remoting Pt.1（RPC/分布式通信）
    10: "architecture",  # Remoting Pt.2
    12: "architecture",  # Concurrency Pt.1
    19: "architecture",  # Concurrency Pt.2
    20: "architecture",  # Interview Michael Stal
    29: "architecture",  # Concurrency Pt.3
    36: "languages",     # Interview Guy Steele（Lisp/Scheme）
    49: "languages",     # Dynamic Languages for Static Minds
    53: "architecture",  # Product Line Engineering Pt.1
    58: "architecture",  # Product Line Engineering Pt.2
    65: "cloud",         # 嵌入式系统（硬件/系统向）
    67: "architecture",  # Roundtable on MDSD 与 PLE
    73: "architecture",  # Real Time Systems（实时系统）
    77: "architecture",  # Fault Tolerance Pt.1（容错/分布式）
    78: "architecture",  # Fault Tolerance Pt.2
    88: "cloud",         # Singularity Research OS（操作系统）
    98: "architecture",  # REST（Stefan Tilkov 分布式风格）
    114: "practice",     # Requirements Engineering（需求工程）
    121: "data",         # OR Mappers（对象关系映射）
    129: "languages",    # F# 语言
    139: "practice",     # Fearless Change（组织变革）
    191: "practice",     # Massively Open Online Courses（教育）
    212: "practice",     # Company Culture（公司文化）
    224: "practice",     # Technical Debt（技术债管理）
    230: "languages",    # NodeJS
    258: "practice",     # Recruiting（招聘）
    265: "practice",     # Becoming a Tech Lead（领导力）
    270: "practice",     # Software Estimation（估算）
    280: "practice",     # Career Strategy（职业策略）
    293: "languages",    # Angular（前端框架）
    297: "architecture", # Blockchain（分布式账本）
    299: "architecture", # Rules Engines（规则引擎）
    310: "practice",     # Performance Optimization（性能工程）
    312: "cloud",        # Internet of Things（物联网）
    317: "practice",     # Measuring Software Engineering（生产力度量）
    323: "languages",    # WebAssembly
    325: "devops",       # Chaos Engineering（混沌工程）
    335: "cloud",        # Edge Computing（边缘计算）
    347: "cloud",        # Load Balancing / HAProxy（负载均衡）
    354: "data",         # ScyllaDB
    356: "architecture", # Truffle / Smart Contracts（智能合约）
    358: "data",         # Probabilistic Data Structures（大数据）
    368: "practice",     # Managing Distributed Teams
    381: "languages",    # Spring Boot -> Java 生态
    410: "practice",     # 日本游戏本地化与移植
    449: "practice",     # Build vs Buy
    459: "practice",     # 游戏/仿真引擎
    465: "practice",     # 97 Things Every Java Programmer（经验随笔）
    478: "security",     # Network Segmentation（网络安全）
    487: "architecture", # Dapr 分布式应用运行时
    494: "languages",    # C 编程（描述中含 security 噪声词）
    519: "practice",     # Building a SaaS（产品/创业向）
    543: "practice",     # 企业中模式与反模式
    574: "practice",     # Software as an Engineering Discipline（工程学科）
    576: "architecture", # Back-ends for Front-ends（架构模式）
    587: "devops",       # Managing Dependency Freshness（依赖维护）
    596: "architecture", # Durable Execution with Temporal（工作流/架构）
    598: "practice",     # AMMERSE 价值框架
    604: "practice",     # Software Requirements Essentials（需求工程）
    609: "practice",     # Software Engineering at Google（工程实践）
    615: "architecture", # Tidy First?（软件设计）
    626: "architecture", # Gen AI for Software Architecture（架构实践向）
    627: "practice",     # Leaders and Software Engineers（职业/管理）
    639: "practice",     # Regulated Industries（合规/业务约束）
    643: "devops",       # Production Readiness（生产就绪/SRE）
    655: "practice",     # Professional Skills（软技能）
    663: "architecture", # Managing External APIs（API 消费管理）
    676: "languages",    # Pydantic（Python 生态）
    687: "cloud",        # Proton and Wine（兼容层/系统）
    695: "practice",     # eBooks 基础设施（出版/写作向）
    699: "devops",       # Internal Dev Platforms（内研平台工程）
    700: "architecture", # 高流量事件的等待队列架构
    701: "practice",     # Readiness in Software Engineering（就绪度/流程）
    704: "practice",     # System Design Interviews（面试向）
    707: "practice",     # ERP 自动化与 AI（企业业务向）
    721: "practice",     # Risk-First Software Development（风险/流程）
    726: "architecture", # Swagger 生态（API 工具）
    731: "practice",     # AI 与工程经理角色（管理向）
    734: "security",     # LLM 数据保护护栏
    2: "architecture",   # Dependencies（早期依赖主题）
    42: "architecture",  # Interview Gregor Hohpe（集成模式）
    388: "architecture", # Decoupled CMS（内容管理系统/架构）
    401: "practice",     # Waterfall Versus Agile（方法论）
    403: "practice",     # Speaking at Tech Conferences（演讲/职业）
    420: "practice",     # Making Scrum Work（方法论）
    435: "data",         # OR Mappers / Entity Framework
    463: "architecture", # Web 3.0 与去中心化协议
    471: "practice",     # Choosing the Right Tech Stack（技术选型）
    472: "practice",     # Handling Customer Issues（客户支持）
    497: "practice",     # Understanding Software Dynamics（软件动态/性能）
    500: "architecture", # Blockchain Interoperability
    531: "cloud",        # Tailscale（VPN）
    546: "architecture", # InterPlanetary File System（IPFS 协议）
    553: "languages",    # Deno（JS 运行时）
    554: "practice",     # Behavioral Code Analysis（代码分析/组织）
    555: "practice",     # Upskilling（技能成长）
    559: "practice",     # Software Obsolescence（软件过时）
    567: "devops",       # GitHub Actions（CI/CD）
    570: "languages",    # jsonnet 语言
}

# 站点动态条目（不是真正的访谈节目）→ practice
SITE_NEWS = {
    "about", "feedback", "feedback and roadmap", "roadmap",
    "announcements and requests", "survey results", "the new website",
    "special episode on the patterns journal",
}

# ---------- 基础解析 ----------

def parse_date(raw):
    return datetime.strptime(raw.strip(), "%a, %d %b %Y %H:%M:%S %z")

def parse_duration(raw):
    parts = [int(x) for x in re.findall(r"\d+", raw or "")]
    if len(parts) >= 3:
        h, m, s = parts[0], parts[1], parts[2]
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 1:
        return parts[0] * 60
    return 0

def format_duration(secs):
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)

PREFIX_RE = re.compile(r"^(?:SE Radio|Episode|Edpisode)\s+(\d{1,4})\s*[:\u2013\u2014-]\s*", re.I)
LEADNUM_RE = re.compile(r"^\s*(\d{1,4})\s*[:：]\s*")
GUEST_ON_RE = re.compile(r"^(?P<g>[\w][\w\s.'\u2019-]{0,60}?)\s+on\s+(?P<t>.{12,})$")
WITH_RE = re.compile(r"^.*\bwith\s+(?P<g>[A-Za-z][\w.'\u2019-]*(?:\s+[A-Za-z][\w.'\u2019-]*){0,3})\s*$")

def split_title(title):
    """返回 (标题中的期数或 None, 去掉前缀后的主题)"""
    m = PREFIX_RE.match(title)
    if m:
        return int(m.group(1)), title[m.end():].strip()
    m = LEADNUM_RE.match(title)
    if m:
        return int(m.group(1)), title[m.end():].strip()
    return None, title.strip()

def extract_guest(rest):
    m = GUEST_ON_RE.match(rest)
    if m:
        g = m.group("g").strip().strip(".,")
        low = g.lower()
        if low not in ("round table", "we", "listeners", "feedback") and "host" not in low:
            return g
    m = WITH_RE.match(rest)
    if m:
        g = m.group("g").strip()
        if "host" not in g.lower():
            return g
    m = re.match(r"^Interview(?: with)?\s+(?P<g>.+)$", rest, re.I)
    if m:
        return m.group("g").strip()
    return ""
# ---------- 描述清洗（白名单标签 + 链接修正 + 去除赞助尾注） ----------

VOID = {"br"}
ALLOWED = {"p", "br", "strong", "b", "em", "i", "code", "ul", "ol", "li", "a", "h3", "h4", "blockquote"}

class _Node:
    __slots__ = ("tag", "attrs", "kids", "text")
    def __init__(self, tag, attrs=None, text=""):
        self.tag = tag
        self.attrs = attrs or {}
        self.kids = []
        self.text = text

class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.stack = [self.root]
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag == "img":
            return
        node = _Node(tag, dict(attrs))
        self.stack[-1].kids.append(node)
        if tag not in VOID:
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        if tag in ("script", "style", "img"):
            return
        self.stack[-1].kids.append(_Node(tag, dict(attrs)))
    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if tag in VOID or self.skip:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        if self.skip or not data:
            return
        self.stack[-1].kids.append(_Node("#text", text=data))

def _fix_href(href):
    if not href:
        return None
    href = href.strip()
    low = href.lower()
    if low.startswith("javascript:") or "trail.gitguardian.com" in low:
        return None
    if "trello.com/team/" in low or "wpengine.com/team/" in low:
        i = href.find("/team/")
        return "https://se-radio.net" + (href[i:] if i >= 0 else "/")
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/") or href.startswith("../"):
        return "https://se-radio.net/" + href.lstrip("./")
    if re.match(r"^https?://", low):
        return href
    return "https://se-radio.net/" + href.lstrip("./")

def _ser(node):
    """序列化为 (html, 纯文本)"""
    if node.tag == "#text":
        return h_escape(node.text), node.text
    if node.tag == "br":
        return "<br>", " "
    buf_h, buf_t = [], []
    for k in node.kids:
        h, t = _ser(k)
        buf_h.append(h)
        buf_t.append(t)
    if node.tag == "a":
        href = _fix_href(node.attrs.get("href"))
        if href:
            return ('<a href="%s" target="_blank" rel="noopener">%s</a>'
                    % (h_escape(href, quote=True), "".join(buf_h))), "".join(buf_t)
        return "".join(buf_h), "".join(buf_t)   # 链接已失效 -> 仅保留文本
    if node.tag in ALLOWED:
        return "<%s>%s</%s>" % (node.tag, "".join(buf_h), node.tag), "".join(buf_t)
    return "".join(buf_h), "".join(buf_t)       # 其它标签解包裹

TAIL_NOISE = [
    re.compile(r"^(bought|rought|brought)?\s?to you by\b"),
    re.compile(r"^this episode( is)? sponsored by\b"),
    re.compile(r"^sponsored by\b"),
]

def _norm(t):
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()

def _tail_noise(text):
    s = text.lower()
    if any(p.match(s) for p in TAIL_NOISE):
        return True
    return ("ieee computer society" in s or "ieee software" in s) and len(s) < 230

def sanitize_desc(raw):
    b = _Builder()
    b.feed(raw or "")
    b.close()
    blocks = []
    for k in b.root.kids:
        h, t = _ser(k)
        t = _norm(t)
        if t:
            blocks.append((h, t))
    while blocks and _tail_noise(blocks[-1][1]):
        blocks.pop()
    html_out = "".join(h for h, _ in blocks)
    text_out = " ".join(t for _, t in blocks)
    return html_out, text_out
# ---------- 分类 ----------

def classify_episode(idx, title, desc_text):
    if idx in OVERRIDES:
        return OVERRIDES[idx]
    t = title.lower()
    for cat, pat in RULES:
        if re.search(pat, t):
            return cat
    d = desc_text.lower()[:4000]
    for cat, pat in RULES:
        if re.search(pat, d):
            return cat
    return DEFAULT_CAT

def main():
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    n_raw = len(raw)

    # 1) 去重：同一 mp3 视为重复发布，保留 pubDate 较新者
    by_mp3 = {}
    for e in raw:
        mp3 = e.get("mp3") or ""
        try:
            cur = parse_date(e["pubDate"])
        except Exception:
            cur = datetime.min
        prev = by_mp3.get(mp3)
        if prev is None:
            by_mp3[mp3] = e
        else:
            try:
                old = parse_date(prev["pubDate"])
            except Exception:
                old = datetime.min
            if cur > old:
                by_mp3[mp3] = e
    deduped = list(by_mp3.values())
    print("== 数据 " + "=" * 62)
    print(f"输入 {n_raw} 条 → 去重后 {len(deduped)} 条（移除 {n_raw - len(deduped)} 条重复）")

    # 1.5) 字幕索引
    sub_index = build_subtitle_index()
    print(f"字幕索引：{len(sub_index)} 个匹配键")

    # 2) 逐条清洗与分类
    records = []
    cat_counts = Counter()
    fallthrough = []
    num_collisions = Counter()
    for e in deduped:
        idx = e.get("index")
        raw_title = (e.get("title") or "").strip()
        num, rest = split_title(raw_title)
        n = num if num is not None else idx
        rest = re.sub(r"\.mp3$", "", rest, flags=re.I).strip()
        guest = extract_guest(rest)
        num_collisions[n] += 1
        try:
            dt = parse_date(e.get("pubDate", ""))
            iso = dt.strftime("%Y-%m-%d")
            ts = int(dt.timestamp())
            year = dt.year
        except Exception:
            iso, ts, year = "", 0, 0
        du = parse_duration(e.get("duration", ""))
        raw_desc = e.get("description") or ""
        dh, dtxt = sanitize_desc(raw_desc)
        trunc = bool(re.search(r"\.\.\.\s*(</p>)?\s*$", raw_desc, re.I))
        if trunc:
            dh += '<p class="desc-note">⚠ 源数据简介不完整（以“...”截断）。</p>'
        if not dtxt:
            dh = '<p class="desc-note">（源数据无简介）</p>'
        if rest.lower() in SITE_NEWS:
            cat = "practice"
        else:
            cat = classify_episode(idx, raw_title, dtxt)
        if cat not in CAT_IDS:
            cat = DEFAULT_CAT
        cat_counts[cat] += 1
        title_hit = any(re.search(pat, raw_title.lower()) for _, pat in RULES)
        if not title_hit:
            fallthrough.append((idx, n, cat, raw_title[:62]))
        # 字幕匹配：从 MP3 URL 提取 basename，查找对应 srt 文件名（播放时动态加载）
        sub_name = None
        mp3_url = e.get("mp3", "")
        if mp3_url and sub_index:
            mp3_base = mp3_url.split("?")[0].split("/")[-1].replace(".mp3", "").lower()
            # 先尝试完整匹配（下划线形式）
            sub_name = sub_index.get(mp3_base)
            # 再尝试连字符转下划线
            if not sub_name:
                sub_name = sub_index.get(mp3_base.replace("-", "_"))
            # 最后尝试用期数前缀匹配
            if not sub_name:
                num_prefix = re.match(r'^(\d+)_', mp3_base)
                if num_prefix:
                    sub_name = sub_index.get(num_prefix.group(1))
        records.append({
            "n": n, "idx": idx, "cat": cat, "ti": rest, "gu": guest,
            "d": iso, "y": year, "ts": ts, "du": du, "dt": format_duration(du),
            "lk": e.get("link", ""), "mp": mp3_url,
            "dh": dh, "tr": trunc,
            "sub": sub_name,
        })

    # 3) 统计与报告
    dates = sorted(r["d"] for r in records if r["d"])
    min_d, max_d = dates[0], dates[-1]
    total_h = sum(r["du"] for r in records) / 3600.0
    print("分类分布：")
    for c in CATEGORIES:
        print(f"  {c['zh']:<14} {cat_counts[c['id']]:>4}")
    n_with_subs = sum(1 for r in records if r.get("sub"))
    print(f"含字幕节目：{n_with_subs} 条")
    dup_n = sorted(k for k, v in num_collisions.items() if v > 1)
    if dup_n:
        print("标题期数相同的条目（不同节目撞号）：", dup_n)
    print("标题未命中规则（仅描述命中或兜底，需复核）：")
    for idx, n, cat, t in fallthrough:
        print(f"  {str(idx):>4} | #{n:>4} | {cat:<12} | {t}")
    print("== 每类抽样（各 5 条） " + "=" * 44)
    by_cat = {c["id"]: [] for c in CATEGORIES}
    for r in records:
        by_cat[r["cat"]].append(r)
    for c in CATEGORIES:
        samples = [(r["n"], r["ti"][:48]) for r in by_cat[c["id"]][:5]]
        print(f"  {c['zh']:<14} -> " + " | ".join(f"#{n} {t}" for n, t in samples))

    # 4) 渲染模板
    tpl = TPL.read_text(encoding="utf-8")
    # 字幕目录相对 dist 输出目录的路径（前端播放时按此路径 fetch srt）
    sub_rel = os.path.relpath(SUBTITLES_DIR, OUT.parent).replace(os.sep, "/")
    if not sub_rel.endswith("/"):
        sub_rel += "/"
    cats_json = json.dumps(CATEGORIES, ensure_ascii=False, separators=(",", ":"))
    # 移除空 sub 字段以减小体积
    for r in records:
        if not r.get("sub"):
            r.pop("sub", None)
    # 节目数据写入独立 JSON 文件（页面使用时 fetch 加载，不内嵌进 HTML）
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    data_text = json.dumps(records, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    DATA_OUT.write_text(data_text, encoding="utf-8")
    html_out = (tpl
                .replace("__CATS_JSON__", cats_json)
                .replace("__DATA_URL__", DATA_OUT.name)
                .replace("__SUB_BASE__", sub_rel)
                .replace("__TOTAL__", str(len(records)))
                .replace("__MIN_DATE__", min_d)
                .replace("__MAX_DATE__", max_d)
                .replace("__HOURS__", "%.0f" % total_h)
                .replace("__BUILD_DATE__", datetime.now().strftime("%Y-%m-%d %H:%M")))
    placeholders = ["__CATS_JSON__", "__DATA_URL__", "__SUB_BASE__",
                    "__TOTAL__", "__MIN_DATE__", "__MAX_DATE__", "__HOURS__", "__BUILD_DATE__"]
    missing = [p for p in placeholders if p in html_out]
    if missing:
        print("警告：以下模板占位符未替换：", missing)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_out, encoding="utf-8")

    # 5) 自检：数据文件可解析、分类合法、数量一致
    ok = True
    emb = None
    try:
        emb = json.loads(DATA_OUT.read_text(encoding="utf-8"))
    except Exception as e:
        print("自检失败：数据文件解析异常：", e); ok = False
    if emb is not None:
        if len(emb) != len(records):
            print("自检失败：数据数量不一致"); ok = False
        bad = sorted({(r["n"], r["cat"]) for r in emb if r["cat"] not in CAT_IDS})
        if bad:
            print("自检失败：非法分类", bad[:10]); ok = False
        if emb and all(r.get("ti") for r in emb[:5]) is False:
            print("自检失败：ti 字段异常"); ok = False
        if not all(isinstance(r["n"], int) for r in emb):
            print("自检失败：n 字段类型异常"); ok = False
    print(f"== 输出 {OUT.name}（{OUT.stat().st_size / 1024:.0f} KB）+ {DATA_OUT.name}（{DATA_OUT.stat().st_size / 1024:.0f} KB）" +
          ("  √ 自检通过" if ok else "  × 自检失败"))

if __name__ == "__main__":
    main()