# -*- coding: utf-8 -*-
"""CS Studio 화면 확인: 저자 실습(여행 일정) 기록과 실제 서비스로 상태별 화면을 캡처한다. 마우스는 쓰지 않는다.
사용: python -X utf8 tests/ui_check.py <캡처폴더>"""
import json, os, sys, time, shutil, subprocess, pathlib, tempfile, urllib.request, urllib.parse
from PIL import ImageGrab

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
SRC = ROOT / ".harness" / "runs" / "author_01"
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_ui_"))
SHOT = pathlib.Path(sys.argv[1]); SHOT.mkdir(parents=True, exist_ok=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("CS_STUDIO_PORT", "CS_AUTO_ACK")}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def http(base, path, body=None):
    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=5).read().decode("utf-8"))


def shot(name, box=(1920, 0, 3840, 1080)):
    ImageGrab.grab(bbox=box, all_screens=True).save(SHOT / f"{name}.png")
    print("캡처", name, flush=True)


def hook(payload):
    return subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "hook.py")], input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", env=ENV, cwd=ROOT)


def main():
    for f in ("context.json", "arch.json"):
        shutil.copy(SRC / f, RUN / f)
    app = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "app.py"), "--x", "1922", "--y", "0", "--w", "1916", "--h", "1040"],
                           env=ENV, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    edge = None
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
        http(base, "/step", {"step": "01", "title": "친구와 여행 일정 공유하기"})
        time.sleep(2.5); shot("ui_00_idle")
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "start", "workspace/app.py"], env=ENV, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        print(r.stdout.strip())
        url = (RUN / "app_url.txt").read_text().strip()
        time.sleep(3); shot("ui_01_service")
        # 실제 요청: 일정 등록 (사람이 폼에서 등록하는 것과 같은 POST)
        data = urllib.parse.urlencode({"destination": "강릉", "days": 2, "people": 3}).encode()
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url + "trips", data=data), timeout=5)
            print("POST /trips →", resp.status)
        except Exception as e:
            print("POST /trips 오류:", e)
        t0 = time.time()
        for i in range(14):
            time.sleep(1.1); shot(f"ui_02_replay_{i:02d}")
        # 코드 변경 표시(실제 파일은 바꾸지 않음: PreToolUse 만 거쳐 화면 표시 → 확인까지)
        src = (WS / "app.py").read_text(encoding="utf-8")
        line = next(l for l in src.splitlines() if "def " in l)
        pl = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(ROOT),
              "tool_input": {"file_path": str(WS / "app.py"), "old_string": line, "new_string": line + "  # 예: 이 함수에 검증을 추가한다면"}}
        h = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "harness" / "hook.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8", env=ENV, cwd=ROOT)
        h.stdin.write(json.dumps(pl)); h.stdin.close()
        for i in range(4):
            time.sleep(1.4); shot(f"ui_03_code_{i}")
        h.wait(60)
        print("코드 표시 후 hook 결과:", "허용" if not h.stdout.read().strip() else "거부", "| app.py 그대로:", (WS / "app.py").read_text(encoding="utf-8") == src)
        time.sleep(2.5); shot("ui_04_back_to_default")
        # 사용자 개입: 일시정지 → 요소 선택 질문
        http(base, "/pause", {"reason": "화면에서 일시정지"}); time.sleep(1.5); shot("ui_05_paused")
        a = http(base, "/arch")
        http(base, "/ask", {"text": "trips.json 은 왜 데이터베이스가 아니라 파일인가요?", "selected": {"type": "구성 요소", "id": "store", "label": a["nodes"]["store"]["label"]}})
        time.sleep(2.5); shot("ui_06_question")
        http(base, "/event", {"type": "EXPLAIN", "data": {"text": "(화면 확인용 예시 답변) 지금 기능은 몇 건 등록과 전체 조회뿐이라 파일로 충분합니다. 동시에 여러 명이 쓰거나 검색이 필요해지면 그때 데이터베이스를 비교합니다."}})
        time.sleep(2); shot("ui_07_answer")
        http(base, "/resume", {})
        # 보조 정보 서랍 + 노트북 해상도: 같은 Studio 를 Edge 앱 창(1366x768)으로 열어 확인
        for tab in ("code", "log"):
            edge = subprocess.Popen([sys.executable, "-X", "utf8", "-c",
                "import webview,sys; webview.create_window('CS Studio 1366', sys.argv[1], width=1366, height=768, x=1930, y=20, on_top=True); webview.start()",
                f"{base}/#open={tab}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(7)
            shot(f"ui_08_laptop_{tab}", (1920, 0, 1920 + 1400, 820))
            subprocess.run(["taskkill", "/PID", str(edge.pid), "/T", "/F"], capture_output=True); edge = None
            time.sleep(1.5)
        evs = http(base, "/events")
        kinds = [e["type"] for e in evs]
        print("이벤트:", {k: kinds.count(k) for k in sorted(set(kinds))})
    finally:
        if edge:
            subprocess.run(["taskkill", "/PID", str(edge.pid), "/T", "/F"], capture_output=True)
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "stop"], env=ENV, cwd=ROOT, capture_output=True)
        app.terminate()
        time.sleep(1.5)
        left = subprocess.run(["powershell", "-NoProfile", "-Command",
                               "(Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*BSP_CAD_REVIT_TUTOR*studio*server.py*' }).Count"], capture_output=True, text=True).stdout.strip()
        print("창을 닫은 뒤 남은 Studio 서버 수:", left or "0")


if __name__ == "__main__":
    main()
