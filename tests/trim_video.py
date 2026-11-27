# -*- coding: utf-8 -*-
"""녹화본에서 Studio 가 다른 창에 가려진 장면을 빼고 이어 붙인다(개인 화면이 영상에 남지 않게).
판정: 각 프레임 맨 위 띠가 Studio(어두운 제목줄)가 아니면 가려진 것. 앞뒤 0.5초 여유를 두고 잘라 낸다.
사용: python -X utf8 tests/trim_video.py <입력.mp4> <출력.mp4>"""
import sys, subprocess, tempfile, pathlib, glob
import imageio_ffmpeg
from PIL import Image

FF = imageio_ffmpeg.get_ffmpeg_exe()
src, dst = sys.argv[1], sys.argv[2]
FPS = 15
tmp = pathlib.Path(tempfile.mkdtemp(prefix="trim_"))
subprocess.run([FF, "-loglevel", "error", "-i", src, "-vf", "scale=192:108", str(tmp / "%06d.png")], check=True)
fs = sorted(glob.glob(str(tmp / "*.png")))
bad = []
for p in fs:
    im = Image.open(p).convert("RGB")
    px = [im.getpixel((x, 2)) for x in range(0, 192, 8)]
    m = sum(sum(c) for c in px) / len(px) / 3
    s = sum(abs(c[0] - c[2]) for c in px) / len(px)
    bad.append(m > 45 or s > 25)
# 가려진 프레임 앞뒤 여유
pad = FPS // 2
cut = bad[:]
for i, b in enumerate(bad):
    if b:
        for k in range(max(0, i - pad), min(len(bad), i + pad + 1)):
            cut[k] = True
segs, start = [], None
for i, c in enumerate(cut + [True]):
    if not c and start is None:
        start = i
    if c and start is not None:
        if i - start >= FPS:          # 1초 미만 조각은 버림
            segs.append((start / FPS, i / FPS))
        start = None
print("전체", len(fs) / FPS, "초 · 가려진 프레임", sum(bad), "· 남길 구간", [(round(a, 1), round(b, 1)) for a, b in segs])
if not segs:
    sys.exit("남길 구간이 없습니다")
parts = "".join(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];" for i, (a, b) in enumerate(segs))
fc = parts + "".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1[v]"
subprocess.run([FF, "-loglevel", "error", "-y", "-i", src, "-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "slow",
                "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst], check=True)
# 결과 재검사
chk = pathlib.Path(tempfile.mkdtemp(prefix="trimchk_"))
subprocess.run([FF, "-loglevel", "error", "-i", dst, "-vf", "scale=192:108", str(chk / "%06d.png")], check=True)
left = 0
for p in sorted(glob.glob(str(chk / "*.png"))):
    im = Image.open(p).convert("RGB")
    px = [im.getpixel((x, 2)) for x in range(0, 192, 8)]
    if sum(sum(c) for c in px) / len(px) / 3 > 45 or sum(abs(c[0] - c[2]) for c in px) / len(px) > 25:
        left += 1
print("결과", dst, "· 남은 가려진 프레임", left)
