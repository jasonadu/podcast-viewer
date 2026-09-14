import glob
import os
import re

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


def download_subtitle_lib(video_url: str, sub_langs=None, output_dir="./subtitles"):
    if sub_langs is None:
        sub_langs = ["en"]
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'skip_download': True,  # 不下载视频
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': sub_langs,
        'convertsubtitles': 'srt',  # 依赖 ffmpeg；缺 ffmpeg 时由下面的兜底逻辑完成转换
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    # 没装 ffmpeg 时 yt-dlp 会跳过 srt 转换，这里兜底处理残留的 .vtt
    convert_stray_vtt_to_srt(output_dir)



def parse_playlist_video_links(playlist_url: str, max_items: int | None = None) -> list[dict]:
    """解析播放列表（list=xxx）下的所有视频链接。

    使用 extract_flat 扁平化解析：只请求播放列表结构（自动处理分页），
    不逐个展开视频页面，速度快、请求少。

    返回 [{'index', 'id', 'title', 'url'}, ...]，url 形如
    https://www.youtube.com/watch?v=VIDEO_ID，可直接传给 download_subtitle_lib。
    """
    ydl_opts = {
        'skip_download': True,
        'extract_flat': 'in_playlist',  # 只解析列表结构，不展开每个视频
        'socket_timeout': 20,
        'retries': 3,
        'quiet': True,
        'no_warnings': True,
    }
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        if info.get('_type') not in ('playlist', 'multi_video'):
            # 传进来的是单个视频
            return [{
                'index': 1,
                'id': info.get('id'),
                'title': info.get('title'),
                'url': info.get('webpage_url') or info.get('original_url'),
            }]
        for index, entry in enumerate(info.get('entries') or [], 1):
            if not entry:
                continue
            url = (entry.get('url')
                   or entry.get('webpage_url')
                   or (f"https://www.youtube.com/watch?v={entry['id']}" if entry.get('id') else None))
            if not url:
                continue
            # print(f"{url} | {entry.get('title')} /n")
            results.append({'index': index, 'id': entry.get('id'), 'title': entry.get('title'), 'url': url})
            if max_items is not None and len(results) >= max_items:
                break
    return results


if __name__ == "__main__":
    playlist_url = "https://www.youtube.com/watch?list=PLHJB2bhmgB7esz0BxMCt1jJwsoaqWtFff"
    links = parse_playlist_video_links(playlist_url)
    print(f'共解析到 {len(links)} 个视频链接：')
    for item in links:
        print(f"{item['index']:>3}. {item['title']}\n     {item['url']}")

    # 如需给整个播放列表下载字幕，取消下面注释即可：
    # for item in links:
    #     download_subtitle_lib(item['url'])
