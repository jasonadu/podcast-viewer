#!/usr/bin/env python3
"""
多线程 YouTube 字幕下载器

读取 data/comp/up.txt (标题 | URL), 对照 data/se_radio_23379.json 获取 mp3 URL,
按 mp3 文件名派生 .srt 文件名, 多线程下载 YouTube 字幕, 保存到 subtitles/ 目录.

用法:
  python -m subtitle_download.main
"""
import glob
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import yt_dlp

# 匹配 VTT 时间戳，如 00:00:01.480 或 01:25.300（无小时段）
_VTT_TS = re.compile(r'(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\.(\d{1,3})')
# YouTube 自动字幕的内联标签，如 <c>、</c>、<00:00:01.480>
_INLINE_TAG = re.compile(r'<[^>]*>')


def _parse_vtt_time(ts: str):
    """解析 VTT 时间串，返回 (SRT时间串, 毫秒数)，失败返回 (None, None)。"""
    m = _VTT_TS.search(ts)
    if not m:
        return None, None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    millis = int(m.group(4).ljust(3, '0'))
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}', ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _parse_vtt_cues(vtt_text: str):
    """解析 VTT 文本，返回 [(start_str, end_str, start_ms, end_ms, text), ...]。"""
    cues = []
    lines = vtt_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if '-->' not in line:
            i += 1
            continue
        left, _, right = line.partition('-->')
        right_tokens = right.split()
        start, start_ms = _parse_vtt_time(left)
        end, end_ms = _parse_vtt_time(right_tokens[0] if right_tokens else '')
        if start is None or end is None:
            i += 1
            continue
        i += 1  # 跳过时间轴行，后面是字幕文本，直到空行
        text_lines = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(_INLINE_TAG.sub('', lines[i]).strip())
            i += 1
        text = '\n'.join(t for t in text_lines if t)
        cues.append((start, end, start_ms, end_ms, text))
    return cues


def vtt_to_srt_text(vtt_text: str) -> str:
    """WebVTT -> SRT。

    YouTube 自动字幕采用"滚动"方式（相邻字幕大量重复同一行文本），
    这里做清理：与上一条相邻（<=1s）时，仅保留新出现的行，
    并跳过内容为空或完全没有新行的字幕。
    """
    blocks = []
    index = 1
    prev_end_ms = None
    prev_lines = set()
    for start, end, start_ms, end_ms, text in _parse_vtt_cues(vtt_text):
        lines = [t for t in text.split('\n') if t]
        if not lines:
            continue
        adjacent = prev_end_ms is not None and start_ms <= prev_end_ms + 1000
        fresh = [t for t in lines if t not in prev_lines] if adjacent else lines
        if not fresh:
            prev_end_ms = max(end_ms, prev_end_ms or 0)
            continue
        blocks.append(f'{index}\n{start} --> {end}\n' + '\n'.join(fresh))
        index += 1
        prev_end_ms = end_ms
        prev_lines = set(fresh)
    return '\n\n'.join(blocks) + '\n' if blocks else ''


def convert_stray_vtt_to_srt(output_dir: str):
    """兜底：把目录里残留的 .vtt 转成 .srt（ffmpeg 可用时 yt-dlp 已转好，此处为空操作）。"""
    for vtt_path in glob.glob(os.path.join(output_dir, '*.vtt')):
        with open(vtt_path, encoding='utf-8-sig') as f:
            srt_text = vtt_to_srt_text(f.read())
        if not srt_text:
            continue
        srt_path = os.path.splitext(vtt_path)[0] + '.srt'
        with open(srt_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(srt_text)
        os.remove(vtt_path)
        print(f'已转换: {os.path.basename(srt_path)}')


# ---------------------------------------------------------------------------
# 原有单视频下载接口 (保留供外部调用)
# ---------------------------------------------------------------------------
def download_subtitle_lib(video_url: str, sub_langs=None, output_dir="./subtitles"):
    """下载单个视频的字幕 (原有接口, 保留兼容)。"""
    if sub_langs is None:
        sub_langs = ["en"]
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': sub_langs,
        'convertsubtitles': 'srt',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    convert_stray_vtt_to_srt(output_dir)


# ---------------------------------------------------------------------------
# 多线程批量下载: up.txt + JSON → subtitles/
# ---------------------------------------------------------------------------
DEFAULT_WORKERS = 3
WAIT_MIN = 1.0
WAIT_MAX = 2.0

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

UP_TXT_PATH = os.path.join(_PROJECT_ROOT, 'data', 'comp', 'up.txt')
JSON_PATH = os.path.join(_PROJECT_ROOT, 'data', 'se_radio_23379.json')
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, 'subtitles')


def _extract_episode_number(title: str):
    """从 up.txt 标题中提取剧集编号, 失败返回 None。"""
    patterns = [
        r'SE[ -]Radio\s+Episode\s+(\d+)',
        r'SE[ -]Radio\s+(\d+)',
        r'SE-Radio-Episode-(\d+)',
        r'Episode\s+(\d+)',
        r'^(\d+)[-_]',
    ]
    for pat in patterns:
        m = re.search(pat, title)
        if m:
            return int(m.group(1))
    return None


def _normalize_title(title: str) -> str:
    """标准化标题用于模糊匹配: 小写, 去标点, 去多余空格。"""
    t = title.lower()
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def parse_up_txt(path: str) -> list:
    """解析 up.txt, 返回 [{'title': str, 'url': str}, ...]。"""
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or '|' not in line:
                continue
            m = re.match(r'^\d+\.\s*(.+)$', line)
            body = m.group(1) if m else line
            title, _, url = body.partition('|')
            title = title.strip()
            url = url.strip()
            if not url.startswith('http'):
                continue
            entries.append({'title': title, 'url': url})
    return entries


def load_json_index(path: str) -> dict:
    """加载 JSON, 返回 {int index -> entry}。重复 index 保留第一条。"""
    with open(path, 'r', encoding='utf-8') as f:
        items = json.load(f)
    index_map = {}
    for item in items:
        idx = item.get('index')
        if idx is not None and idx not in index_map:
            index_map[idx] = item
    return index_map


def derive_srt_name(mp3_url: str) -> str:
    """从 mp3 URL 派生 srt 文件名: 取 basename, 去掉 .mp3 和 query string, 加 .srt。"""
    basename = os.path.basename(urlparse(mp3_url).path)
    name = re.sub(r'\.mp3$', '', basename, flags=re.IGNORECASE)
    return name + '.srt'


def match_entry(title: str, index_map: dict):
    """匹配 up.txt 标题到 JSON 条目, 返回 entry 或 None。"""
    # 策略1: 按剧集编号
    ep_num = _extract_episode_number(title)
    if ep_num is not None:
        hit = index_map.get(ep_num)
        if hit is not None:
            return hit

    # 策略2: 按标题关键词模糊匹配
    norm = _normalize_title(title)
    words = [w for w in norm.split() if len(w) > 3]
    if len(words) >= 2:
        for entry in index_map.values():
            json_norm = _normalize_title(entry.get('title', ''))
            if all(w in json_norm for w in words):
                return entry

    return None
    return None


def download_one(task: dict) -> dict:
    """下载单个字幕。

    返回 {'status': 'ok'|'fail', 'name': str, 'error': str}
    """
    url = task['url']
    srt_path = task['srt_path']
    srt_name = task['srt_name']
    base = os.path.splitext(srt_path)[0]

    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'outtmpl': base + '.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        return {'status': 'fail', 'name': srt_name, 'error': str(e)}

    # 检查是否已生成目标 srt (ffmpeg 可用时 yt-dlp 直接转好)
    if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
        return {'status': 'ok', 'name': srt_name}
    
    # 兜底: 查找并转换 .vtt → .srt
    converted = False
    for vtt in glob.glob(base + '*.vtt'):
        try:
            with open(vtt, encoding='utf-8-sig') as f:
                srt_text = vtt_to_srt_text(f.read())
            if srt_text:
                with open(srt_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(srt_text)
                converted = True
        finally:
            try:
                os.remove(vtt)
            except OSError:
                pass

    if converted:
        return {'status': 'ok', 'name': srt_name}

    # 查找由 ffmpeg 生成的 .srt (可能带语言代码如 .en)
    for ext_srt in glob.glob(base + '*.srt'):
        if ext_srt != srt_path and os.path.exists(ext_srt):
            try:
                os.replace(ext_srt, srt_path)
                return {'status': 'ok', 'name': srt_name}
            except OSError as e:
                return {'status': 'fail', 'name': srt_name, 'error': str(e)}

    return {'status': 'fail', 'name': srt_name, 'error': 'no subtitle generated'}


def main():
    print('[info] 加载数据文件...')
    entries = parse_up_txt(UP_TXT_PATH)
    index_map = load_json_index(JSON_PATH)
    print(f'[info] up.txt: {len(entries)} 条, JSON: {len(index_map)} 条')

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 构建下载任务列表
    tasks = []
    skipped_no_match = 0
    skipped_no_mp3 = 0
    skipped_exists = 0

    for entry in entries:
        title = entry['title']
        url = entry['url']

        json_entry = match_entry(title, index_map)
        if json_entry is None:
            print(f'[SKIP] 未找到匹配: {title}')
            skipped_no_match += 1
            continue

        mp3_url = json_entry.get('mp3', '')
        if not mp3_url:
            print(f'[SKIP] 无 mp3: {title}')
            skipped_no_mp3 += 1
            continue

        srt_name = derive_srt_name(mp3_url)
        srt_path = os.path.join(OUTPUT_DIR, srt_name)

        if os.path.exists(srt_path):
            skipped_exists += 1
            continue

        tasks.append({
            'title': title,
            'url': url,
            'srt_path': srt_path,
            'srt_name': srt_name,
        })

    print(f'[info] 待下载: {len(tasks)} 条, 已存在跳过: {skipped_exists}, '
          f'无匹配跳过: {skipped_no_match}, 无 mp3 跳过: {skipped_no_mp3}')

    if not tasks:
        print('[info] 没有需要下载的任务.')
        return

    # 多线程下载
    results_ok = 0
    results_fail = 0
    lock = threading.Lock()

    def do_download(task):
        nonlocal results_ok, results_fail
        result = download_one(task)
        with lock:
            if result['status'] == 'ok':
                results_ok += 1
                print(f'  [OK] {task["srt_name"]}')
            else:
                results_fail += 1
                print(f'  [FAIL] {task["srt_name"]}: {result.get("error", "")}')
        time.sleep(random.uniform(WAIT_MIN, WAIT_MAX))
        return result

    print(f'[info] 开始下载 (并发 {DEFAULT_WORKERS})...\n')
    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
        futures = [executor.submit(do_download, task) for task in tasks]
        for f in futures:
            try:
                f.result()
            except Exception as e:
                with lock:
                    results_fail += 1
                print(f'  [ERROR] {e}')

    print(f'\n[info] 完成: 成功 {results_ok}, 失败 {results_fail}')


if __name__ == '__main__':
    main()

