#!/usr/bin/env python3
"""
Substack 转录 -> LRC 字幕转换器

支持三种输入:
  1. 直接的 transcription.json URL / 本地 JSON 文件
  2. Substack 文章页面 URL (自动解析 Transcript 按钮对应的 transcription.json)
  3. 批量 JSON 文件 (如 458709_podcast_items.json), 解析所有 page 属性批量转换

用法:
  python3 substack_to_lrc.py <url_or_file> [output]
示例:
  # 文章页 -> 自动提取转录 URL -> 生成 LRC
  python3 substack_to_lrc.py "https://newsletter.pragmaticengineer.com/p/from-chrome-devtools-to-ai-engineering"

  # 直接给 transcription.json URL
  python3 substack_to_lrc.py "https://substackcdn.com/.../transcription.json?..." out.lrc

  # 批量: 解析 JSON 中所有 page, 输出到目录, 文件名为 page slug.lrc
  python3 substack_to_lrc.py 458709_podcast_items.json ./lrc_output

  # 本地文件
  python3 substack_to_lrc.py transcription.json out.lrc

输入 JSON 格式: 数组, 每项含 start / end / text (秒为单位)
输出 LRC 格式: [mm:ss.xx]text  (时间取 start)
"""
import json
import sys
import os
import re
import tempfile
import urllib.request
import urllib.parse
from collections import Counter


# ---------------------------------------------------------------------------
# 文章页 -> transcription.json URL 解析
# ---------------------------------------------------------------------------
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def is_substack_article_url(url: str) -> bool:
    """判断是否是 Substack 文章页面 URL."""
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    parsed = urllib.parse.urlparse(url)
    if "transcription.json" in parsed.path or "unaligned_transcription" in parsed.path:
        return False
    return "/p/" in parsed.path


def _extract_current_post_id(html: str) -> str:
    """从页面 HTML 中推断当前文章的 post_id."""
    m = re.search(r'"id"\s*:\s*(\d{7,})', html)
    if m:
        return m.group(1)
    ids = re.findall(r'/post/(\d+)/', html)
    if ids:
        return Counter(ids).most_common(1)[0][0]
    return ""


def resolve_transcription_url(article_url: str) -> str:
    """抓取 Substack 文章页, 解析 Transcript 按钮对应的 transcription.json URL."""
    print(f"[info] 抓取文章页: {article_url[:90]}...", file=sys.stderr)
    req = urllib.request.Request(article_url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    aligned = re.findall(r'https?://[^\s"\'<>\\]+/transcription\.json[^\s"\'<>\\]+', html)
    unaligned = re.findall(r'https?://[^\s"\'<>\\]+/unaligned_transcription\.json[^\s"\'<>\\]+', html)
    candidates = aligned or unaligned
    if not candidates:
        raise ValueError("页面中未找到 transcription.json URL, 该文章可能没有播客转录")

    seen = set()
    unique = []
    for u in candidates:
        u = u.rstrip("\\/,.;")
        if u not in seen:
            seen.add(u)
            unique.append(u)

    chosen = unique[0]
    if len(unique) > 1:
        post_id = _extract_current_post_id(html)
        if post_id:
            for u in unique:
                if f"/post/{post_id}/" in u:
                    chosen = u
                    break
    kind = "对齐版" if "/transcription.json" in chosen else "未对齐版"
    print(f"[info] 解析到转录 URL ({kind}): {chosen[:100]}...", file=sys.stderr)
    return chosen


# ---------------------------------------------------------------------------
# 批量 JSON 解析
# ---------------------------------------------------------------------------
def is_batch_json_file(source: str) -> bool:
    """判断输入是否是包含 page 数组的 JSON 文件 (如 458709_podcast_items.json)."""
    if source.startswith("http://") or source.startswith("https://"):
        return False
    if not os.path.isfile(source):
        return False
    if not source.lower().endswith(".json"):
        return False
    try:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) > 0:
            # 检查第一个元素是否有 page 属性
            return isinstance(data[0], dict) and "page" in data[0]
    except (json.JSONDecodeError, OSError):
        pass
    return False


def extract_pages(json_path: str) -> list:
    """从批量 JSON 文件中提取所有 page URL, 去重保序."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pages = []
    seen = set()
    for item in data:
        if isinstance(item, dict):
            page = item.get("page", "").strip()
            if page and page not in seen:
                seen.add(page)
                pages.append(page)
    return pages


def slug_from_page(page_url: str) -> str:
    """从 page URL 提取最后一个 / 后的 slug, 清理非法文件名字符."""
    path = urllib.parse.urlparse(page_url).path
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    slug = "".join(c for c in slug if c not in '\\/:*?"<>|')
    return slug or "untitled"


# ---------------------------------------------------------------------------
# 下载 & 解析 & 生成 LRC
# ---------------------------------------------------------------------------
def fetch_to_file(source: str, referer: str = "") -> str:
    """从 URL 或本地路径获取内容, 返回本地文件路径."""
    if source.startswith("http://") or source.startswith("https://"):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp_path = tmp.name
        tmp.close()
        print(f"[info] 流式下载转录数据: {source[:80]}...", file=sys.stderr)
        headers = {"User-Agent": _UA}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(source, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_path, "wb") as f:
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
            print(f"[info] 下载完成, 共 {total} 字节", file=sys.stderr)
        return tmp_path
    else:
        if not os.path.exists(source):
            raise FileNotFoundError(f"文件不存在: {source}")
        return source


def parse_segments(json_path: str):
    """解析 JSON, 产出 (start, end, text) 迭代器."""
    try:
        import ijson  # type: ignore
        print("[info] 使用 ijson 流式解析", file=sys.stderr)
        with open(json_path, "rb") as f:
            for item in ijson.items(f, "item"):
                start = item.get("start")
                end = item.get("end")
                text = item.get("text", "")
                if start is not None and text:
                    yield float(start), float(end) if end is not None else None, str(text).strip()
        return
    except ImportError:
        pass

    print("[info] ijson 未安装, 使用 json.load 解析 (pip install ijson 可启用流式)", file=sys.stderr)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("JSON 顶层不是数组, 无法解析")
    for item in data:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        text = item.get("text", "")
        if start is not None and text:
            yield float(start), float(end) if end is not None else None, str(text).strip()


def fmt_time(seconds: float) -> str:
    """秒 -> LRC 时间戳 [mm:ss.xx]"""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def to_lrc(segments, output_path: str, title: str = "") -> int:
    """把分段写入 LRC 文件, 返回行数."""
    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        if title:
            f.write(f"[ti:{title}]\n")
        f.write("[re:substack_to_lrc.py]\n\n")
        for start, _end, text in segments:
            f.write(f"{fmt_time(start)}{text}\n")
            count += 1
    return count


def _default_output_name(source: str) -> str:
    """根据输入源生成默认输出文件名."""
    if source.startswith("http://") or source.startswith("https://"):
        path = urllib.parse.urlparse(source).path
    else:
        path = source
    base = os.path.splitext(os.path.basename(path))[0]
    if not base or base == "transcription":
        base = "transcript"
    return f"{base}.lrc"


def process_single(source: str, output_path: str, referer: str = "") -> dict:
    """处理单个输入 (文章页 URL / transcription.json URL / 本地文件), 生成 LRC.
    返回 {"status": "ok"/"fail", "output": str, "error": str, "count": int}"""
    tmp_path = None
    try:
        # 如果是文章页, 先解析 transcription.json
        actual_referer = referer
        if is_substack_article_url(source):
            actual_referer = source
            source = resolve_transcription_url(source)

        json_path = fetch_to_file(source, referer=actual_referer)
        if json_path != source:
            tmp_path = json_path

        segments = list(parse_segments(json_path))
        if not segments:
            return {"status": "fail", "output": output_path, "error": "未解析到任何字幕段", "count": 0}

        count = to_lrc(segments, output_path)
        duration = segments[-1][0]
        print(f"[ok] 生成 {output_path}  共 {count} 行, 末段 {fmt_time(duration).strip('[]')}", file=sys.stderr)
        return {"status": "ok", "output": output_path, "error": "", "count": count}
    except Exception as e:
        print(f"[fail] {output_path}: {e}", file=sys.stderr)
        return {"status": "fail", "output": output_path, "error": str(e), "count": 0}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def process_batch(json_path: str, output_dir: str) -> dict:
    """批量处理 JSON 文件中所有 page 属性.
    返回 {"total": int, "ok": int, "fail": int, "results": list}"""
    pages = extract_pages(json_path)
    if not pages:
        return {"total": 0, "ok": 0, "fail": 0, "results": []}

    os.makedirs(output_dir, exist_ok=True)
    print(f"[info] 批量模式: 共 {len(pages)} 个 page, 输出目录: {output_dir}", file=sys.stderr)

    results = []
    ok_count = 0
    fail_count = 0
    for i, page_url in enumerate(pages, 1):
        if i < 42:
           continue
        slug = slug_from_page(page_url)
        output_path = os.path.join(output_dir, f"{slug}.lrc")
        print(f"\n[{i}/{len(pages)}] {slug}", file=sys.stderr)

        # 已存在则跳过
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            print(f"[skip] 已存在: {output_path}", file=sys.stderr)
            results.append({"page": page_url, "output": output_path, "status": "skip"})
            ok_count += 1
            continue

        result = process_single(page_url, output_path)
        result["page"] = page_url
        results.append(result)
        if result["status"] == "ok":
            ok_count += 1
        else:
            fail_count += 1

    print(f"\n[info] 批量完成: 成功 {ok_count}, 失败 {fail_count}, 总计 {len(pages)}", file=sys.stderr)
    return {"total": len(pages), "ok": ok_count, "fail": fail_count, "results": results}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    source = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) >= 3 else None

    # 批量模式: 输入是包含 page 数组的 JSON 文件
    if is_batch_json_file(source):
        output_dir = output if output else "./lrc_output"
        result = process_batch(source, output_dir)
        if result["fail"] > 0:
            sys.exit(2)
        sys.exit(0)

    # 单文件模式
    if output is None:
        # 如果是文章页, 输出文件名用 page slug
        if is_substack_article_url(source):
            output = f"{slug_from_page(source)}.lrc"
        else:
            output = _default_output_name(source)

    result = process_single(source, output)
    if result["status"] != "ok":
        sys.exit(2)


if __name__ == "__main__":
    main()
