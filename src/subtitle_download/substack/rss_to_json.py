#!/usr/bin/env python3
"""
Substack RSS feed -> 播客条目 JSON 数组

解析 channel > item 列表, 提取:
  link          -> page
  enclosure@url -> mp3
  title(CDATA)  -> name

支持两种输入文件:
  1. 标准 RSS XML
  2. 浏览器打开 XML 后保存的 HTML 页面 (自动提取 <rss>...</rss> 并清理注入元素)

用法:
  python3 rss_to_json.py <rss_file> [output.json]
"""
import json
import re
import sys
import os
import xml.etree.ElementTree as ET


def _load_xml(source: str) -> ET.ElementTree:
    """加载 XML, 兼容标准 RSS 和浏览器渲染的 XML 页面."""
    # 先尝试直接解析
    try:
        return ET.parse(source)
    except ET.ParseError:
        pass

    # 直接解析失败, 尝试从浏览器渲染页面中提取 <rss>...</rss>
    with open(source, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    rss_start = raw.find("<rss")
    rss_end = raw.rfind("</rss>")
    if rss_start == -1 or rss_end == -1:
        raise ValueError("文件中未找到 <rss>...</rss> 片段, 无法解析")

    xml_content = raw[rss_start : rss_end + len("</rss>")]
    # 清理浏览器扩展注入的元素 (如 <div id="__caoliao-browser-ext-root"/>)
    xml_content = re.sub(r'<div[^>]*/>', "", xml_content)

    return ET.ElementTree(ET.fromstring(xml_content))


def parse_rss(source: str) -> list:
    """解析 RSS, 返回 [{page, mp3, name}, ...]"""
    tree = _load_xml(source)
    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS 中未找到 <channel>")

    items = channel.findall("item")
    result = []
    for item in items:
        # link -> page
        link_elem = item.find("link")
        page = link_elem.text.strip() if link_elem is not None and link_elem.text else ""

        # enclosure@url -> mp3
        enc_elem = item.find("enclosure")
        mp3 = enc_elem.get("url", "") if enc_elem is not None else ""

        # title (CDATA) -> name
        title_elem = item.find("title")
        name = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

        result.append({"page": page, "mp3": mp3, "name": name})

    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    source = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) >= 3 else "podcast_items.json"

    if not os.path.exists(source):
        print(f"[error] 文件不存在: {source}", file=sys.stderr)
        sys.exit(1)

    items = parse_rss(source)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[ok] 生成 {output}  共 {len(items)} 条", file=sys.stderr)


if __name__ == "__main__":
    main()
