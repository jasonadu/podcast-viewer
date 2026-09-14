"""临时测试:播放列表解析,逐行写日志防丢失。验证后删除。"""
import time

log = open('_playlist_out.txt', 'a', encoding='utf-8', buffering=1)  # 行缓冲,边跑边写
log.write(f'--- run {time.strftime("%H:%M:%S")} ---\n')
log.write('importing main ...\n')
from subtitle_download.youtube_list import parse_playlist_video_links

log.write('import ok, start parsing ...\n')
try:
    links = parse_playlist_video_links(
        "https://www.youtube.com/watch?list=PLHJB2bhmgB7esz0BxMCt1jJwsoaqWtFff")
    log.write(f'parsed {len(links)} videos\n')
    for it in links:
        log.write(f"{it['index']}. {it['title']} | {it['url']}\n")
    log.write('DONE\n')
except Exception as e:
    log.write(f'ERROR: {type(e).__name__}: {e}\n')
log.close()
