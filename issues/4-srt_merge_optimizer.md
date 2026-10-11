使用 src\subtitle_download\srt_merge_optimizer.py 处理一下下面文件
```
data\subtitles\342-hausenblas-final.srt
data\subtitles\342-istvan-lam-final.srt
data\subtitles\353-bernd-rucker_microservices.srt
data\subtitles\363-Jonathan-Boccara-on-Understanding-Legacy-Code.srt
data\subtitles\364-Peter-Zaitsev-on-Choosing-the-Right-Open-Source-Database.srt
data\subtitles\365-Thorsten-Ball-on-Building-an-Interpreter.srt
data\subtitles\394-Chris-McCord-on-Phoenix-LiveView.srt
```
目的：
合并连续碎字幕片段，取该组第一条开始时间、本组最后一条结束时间；文本拼接成完整语义句子；重新生成 SRT 序号 源字幕问题：ASR 逐词切割，大量条目时长几十毫秒，一句话被拆成多条；遇到 [music] 保留；遇到说话人切换、问答轮转做切分；以完整英文句号.、问号？作为主要语义分割边界。

---
优化当前目录下面的所有srt字幕文件
结果保存到目录 optimized/


合并连续碎字幕片段，取该组第一条开始时间、本组最后一条结束时间；文本拼接成完整语义句子；重新生成 SRT 序号 源字幕问题：ASR 逐词切割，大量条目时长几十毫秒，一句话被拆成多条；遇到 [music] 保留；遇到说话人切换、问答轮转做切分；以完整英文句号.、问号？作为主要语义分割边界。
