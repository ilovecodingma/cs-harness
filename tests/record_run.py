# -*- coding: utf-8 -*-
"""하네스 실제 실행 녹화: 실제 Studio 창 + 실제 Claude 로 다음 구간을 개발하며 오른쪽 모니터를 ffmpeg 로 녹화한다.
지난 구간(author_01)의 문맥·구조를 이어받는다. 결과 기록은 .harness/runs/<이름>/ 에 복사한다.
사용: python -X utf8 tests/record_run.py <출력폴더> <기록이름> "<이번 구간 지시>" """
import json, os, sys, time, shutil, subprocess, pathlib, tempfile, urllib.request
import imageio_ffmpeg

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_rec_"))
OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
NAME = sys.argv[2]
TASK = sys.argv[3]
SRC = ROOT / ".harness" / "runs" / (sys.argv[4] if len(sys.argv) > 4 else "author_01")   # 이어받을 지난 구간 기록
STEP = sys.argv[5] if len(sys.argv) > 5 else "02"
ENV = {k: v for k, v in os.environ.items() if k not in ("CS_STUDIO_PORT", "CS_AUTO_ACK")}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8")
LIMIT = 1800


def http(base, path, body=None):
    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=5).read().decode("utf-8"))


def main():
    for f in ("context.json", "arch.json"):
        shutil.copy(SRC / f, RUN / f)
    app = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "app.py"), "--x", "1922", "--y", "0", "--w", "1916", "--h", "1040"],
                           env=ENV, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec = None
    try:
        for _ in range(100):
            if (RUN / "port.txt").exists():
                break
            time.sleep(0.2)
        base = "http://127.0.0.1:" + (RUN / "port.txt").read_text().strip()
        for _ in range(60):
            if http(base, "/state").get("viewers", 0) > 0:
                break
            time.sleep(0.5)
        http(base, "/step", {"step": STEP, "title": TASK.split("|")[0]})
        video = OUT / f"{NAME}.mp4"
        rec = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "gdigrab", "-framerate", "15", "-offset_x", "1920", "-offset_y", "0",
                                "-video_size", "1920x1080", "-draw_mouse", "0", "-i", "desktop", "-c:v", "libx264", "-preset", "ultrafast",
                                "-crf", "20", "-pix_fmt", "yuv420p", str(video)],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open(OUT / f"{NAME}_ffmpeg.log", "w"))
        time.sleep(3)
        prompt = ("CLAUDE.md 의 원칙과 흐름대로 진행해. 질문하지 말고 바로 진행해. 저자 모드 다음 구간이다. "
                  "지난 구간들의 결과는 workspace/ 에 있고 목적·기능·선택·구조는 Studio 문맥(ctx)·구조(arch)에 남아 있다. 먼저 현재 코드와 데이터를 확인해. "
                  f"이번 구간: {TASK.split('|')[-1]} "
                  "목적에서 기능을, 기능에서 기술을 도출하고, 기존 방식으로 먼저 돌려 실제로 겪은 한계가 있을 때만 새 기술을 비교해. 구조 변화는 arch.py 로, 서비스는 run.py 로 띄워 "
                  "실제 요청으로 동작을 확인한 뒤 목적 달성을 검증해. 설명은 짧게. 끝나면 서비스는 켜 둔 채로 끝내.")
        cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--allowedTools", "Write", "Edit", "Bash(python:*)", "--max-turns", "90"]
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", shell=(os.name == "nt"))
        while proc.poll() is None and time.time() - t0 < LIMIT:
            time.sleep(2)
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            print("시간 초과로 중단")
        out = proc.stdout.read()
        dev = time.time() - t0
        # 다 만든 뒤 사람처럼 실제 요청 한 번 → 재생 장면을 녹화에 남김
        url = (RUN / "app_url.txt").read_text().strip() if (RUN / "app_url.txt").exists() else None
        if url:
            try:
                urllib.request.urlopen(url, timeout=5).read()
            except Exception as e:
                print("마지막 조회 오류:", e)
        time.sleep(15)
        rec.stdin.write(b"q"); rec.stdin.flush(); rec.wait(60); rec = None
        evs = http(base, "/events")
        kinds = {}
        for e in evs:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        print("=== Claude ===\n" + out.strip()[-2500:])
        print("\n=== 이벤트 ===", kinds)
        print("\n=== 문맥 ===", json.dumps(http(base, "/context"), ensure_ascii=False)[:3000])
        dst = ROOT / ".harness" / "runs" / NAME
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(RUN, dst)
        print(f"\n개발 {dev:.0f}초 · 영상 {video} · 기록 {dst}")
    finally:
        if rec:
            try:
                rec.stdin.write(b"q"); rec.stdin.flush(); rec.wait(30)
            except Exception:
                rec.kill()
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "stop"], env=ENV, cwd=ROOT, capture_output=True)
        app.terminate()


if __name__ == "__main__":
    main()
