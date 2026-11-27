# -*- coding: utf-8 -*-
"""흐름도 화면 확인: author_02 기록(문맥·구조)과 실제 서비스로, 실제 요청(장소 추가) 한 번의 재생을 캡처한다.
사용: python -X utf8 tests/ui_flow_check.py <캡처폴더>"""
import json, os, sys, time, shutil, subprocess, pathlib, tempfile, urllib.request, urllib.parse
from PIL import ImageGrab

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / ".harness" / "runs" / "author_02"
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_flow_"))
SHOT = pathlib.Path(sys.argv[1]); SHOT.mkdir(parents=True, exist_ok=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("CS_STUDIO_PORT", "CS_AUTO_ACK")}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8")


def http(base, path, body=None):
    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=5).read().decode("utf-8"))


def shot(name):
    ImageGrab.grab(bbox=(1920, 0, 3840, 1080), all_screens=True).save(SHOT / f"{name}.png")


def main():
    for f in ("context.json", "arch.json"):
        shutil.copy(SRC / f, RUN / f)
    app = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "app.py"), "--x", "1922", "--y", "0", "--w", "1916", "--h", "1040"],
                           env=ENV, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        http(base, "/step", {"step": "02", "title": "여행 동선 정리하기"})
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "start", "workspace/app.py"], env=ENV, cwd=ROOT, capture_output=True)
        url = (RUN / "app_url.txt").read_text().strip().rstrip("/")
        urllib.request.urlopen(url + "/", timeout=5).read()
        urllib.request.urlopen(url + "/trips/1", timeout=5).read()
        time.sleep(3); shot("f_00_idle")
        data = urllib.parse.urlencode({"day": 3, "place": "우도"}).encode()
        r = urllib.request.urlopen(urllib.request.Request(url + "/trips/1/stops", data=data), timeout=5)
        print("POST /trips/1/stops →", r.status)
        for i in range(16):
            time.sleep(1.0); shot(f"f_01_{i:02d}")
        try:
            urllib.request.urlopen(urllib.request.Request(url + "/trips/1/stops", data=urllib.parse.urlencode({"day": 9, "place": "마라도"}).encode()), timeout=5)
        except urllib.error.HTTPError as e:
            print("범위 밖 →", e.code)
        for i in range(16):
            time.sleep(1.0); shot(f"f_02_{i:02d}")
    finally:
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "stop"], env=ENV, cwd=ROOT, capture_output=True)
        app.terminate()


if __name__ == "__main__":
    main()
