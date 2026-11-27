# -*- coding: utf-8 -*-
"""실제 CS Studio 프로그램 창 + 실제 Claude 로 강의 흐름을 돌려 보고, 화면을 캡처해 확인한다.
- 창은 오른쪽 모니터(x=1920)에 띄운다. 마우스는 건드리지 않는다.
- 판정: 코드가 화면에 먼저 나오고(PRESENT) 화면이 다 보여 준 뒤(ack) 실제 파일이 생기는가.
사용: python -X utf8 tests/demo_app.py [캡처폴더]"""
import json, os, sys, time, subprocess, pathlib, tempfile, urllib.request
from PIL import ImageGrab

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_demo_"))
SHOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else RUN)
SHOT.mkdir(parents=True, exist_ok=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("CS_STUDIO_PORT", "CS_AUTO_ACK")}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8")
TARGET = WS / "trips.py"


def http(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=3).read().decode("utf-8"))


def main():
    if TARGET.exists():
        TARGET.unlink()
    app = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "app.py"), "--x", "1930", "--y", "10", "--w", "1880", "--h", "1020"],
                           env=ENV, cwd=ROOT)
    try:
        for _ in range(100):
            if (RUN / "port.txt").exists():
                break
            time.sleep(0.2)
        base = "http://127.0.0.1:" + (RUN / "port.txt").read_text().strip()
        for _ in range(60):                       # 창이 떠서 화면이 접속할 때까지
            if http(base, "/state").get("viewers", 0) > 0:
                break
            time.sleep(0.5)
        print("화면 접속:", http(base, "/state").get("viewers"))
        http(base, "/step", {"step": "00-demo", "title": "첫 파이썬 파일"})
        http(base, "/speed", {"speed": 2})
        prompt = ("CLAUDE.md 의 원칙과 흐름대로 진행해. 질문하지 말고 바로 진행해. 이건 하네스 화면 검증용 짧은 구간이다. "
                  "서비스 목적: 친구들과 여행 일정을 함께 정리하는 서비스. "
                  "이번 구간 목적: 등록한 여행 일정을 나중에 다시 볼 수 있다. "
                  "workspace/ 에 파이썬으로 일정을 등록·조회하는 아주 작은 프로그램을 만들되, 먼저 가장 단순한 기존 방식으로 만들고 실제로 두 번 실행해서 "
                  "그 방식의 한계가 실제로 드러나는지 확인해. 한계가 드러나면 대안을 비교하고 하나를 골라 적용한 뒤 다시 실행해서 목적 달성을 검증해. "
                  "코드는 블록 하나씩, 블록마다 직전에 say.py 로 어떤 기능을 위해 왜 쓰는지 설명. 문맥은 ctx.py 로 빠짐없이 남겨. 전체를 짧게 유지해.")
        cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
               "--allowedTools", "Write", "Edit", "Bash(python:*)", "--max-turns", "40"]
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", shell=(os.name == "nt"))
        n = 0
        last_shot = 0
        file_seen_during_present = False
        while proc.poll() is None and time.time() - t0 < 360:
            evs = http(base, "/events")
            pres = [e for e in evs if e["type"] == "PRESENT"]
            if pres and n < 12 and time.time() - last_shot > 12:
                last_shot = time.time()
                if True:
                    ImageGrab.grab(bbox=(1920, 0, 3840, 1080), all_screens=True).save(SHOT / f"present_{n}.png")
                    n += 1
            time.sleep(1.2)
        out = proc.communicate(timeout=60)[0]
        time.sleep(2)
        ImageGrab.grab(bbox=(1920, 0, 3840, 1080), all_screens=True).save(SHOT / "final.png")
        evs = http(base, "/events")
        print("=== Claude ===\n" + out.strip()[-800:])
        print("\n=== 이벤트 ===")
        for e in evs:
            print(f" {e['id']:>2} +{e['ts'] - t0:5.1f}s {e['type']:<14} {json.dumps(e['data'], ensure_ascii=False)[:80]}")
        tp = next((e["ts"] for e in evs if e["type"] == "PRESENT" and e["data"].get("kind") == "write"), None)
        tc = next((e["ts"] for e in evs if e["type"] == "CREATE_FILE"), None)
        print("\n=== 판정 ===")
        kinds = {e["data"].get("kind") for e in evs if e["type"] == "CONTEXT"}
        code = [e for e in evs if e["type"] in ("CREATE_FILE", "WRITE_CODE", "MODIFY_CODE")]
        ctxj = http(base, "/context")
        print(chr(10) + "=== 문맥 ===" + chr(10) + json.dumps(ctxj, ensure_ascii=False, indent=1)[:2500])
        res = [("코드가 화면에 먼저 나옴(PRESENT)", tp is not None),
               ("화면이 다 보여 준 뒤 파일 생성", tp is not None and tc is not None and tc - tp >= 1.5),
               ("목적·기능을 먼저 정함", {"goal", "feature"} <= kinds and min(e["id"] for e in evs if e["type"] == "CONTEXT" and e["data"].get("kind") == "feature") < min((e["id"] for e in code), default=10**9)),
               ("기존 방식의 한계를 기록", bool({"limit", "problem"} & kinds)),
               ("대안 비교와 선택 이유를 기록", {"option", "decide"} <= kinds),
               ("목적 달성 검증을 기록", "verify" in kinds),
               ("모든 코드 이벤트가 목적·기능에 연결", bool(code) and all(e["ctx"]["goal"] and e["ctx"]["feature"] for e in code)),
               ("PRESENT 마다 창을 앞으로 가져옴(FOCUS ok)", bool([e for e in evs if e["type"] == "FOCUS"]) and all(e["status"] == "ok" for e in evs if e["type"] == "FOCUS")),
               ("설명(EXPLAIN)이 화면에 올라감", any(e["type"] == "EXPLAIN" for e in evs)),
               ("코드를 블록 여러 번에 나눠 보여 줌", sum(1 for e in evs if e["type"] == "PRESENT" and e["data"].get("kind") in ("write", "edit")) >= 2),
               ("코드 블록마다 직전에 설명이 있음", all(any(x["type"] == "EXPLAIN" for x in evs[max(0, i - 3):i]) for i, e in enumerate(evs) if e["type"] == "PRESENT" and e["data"].get("kind") in ("write", "edit"))),
               ("실행 결과가 실제 출력으로 기록", any(e["type"] == "COMMAND_OUTPUT" and e["data"].get("stdout") for e in evs))]
        if tp and tc:
            print(f"  화면에서 코드를 보여 준 시간: {tc - tp:.1f}초")
        for name, c in res:
            print(("통과 " if c else "실패 ") + name)
        print("캡처:", SHOT)
    finally:
        app.terminate()
        for f in WS.iterdir():
            if f.name != ".gitkeep":
                (f.unlink() if f.is_file() else __import__("shutil").rmtree(f))


if __name__ == "__main__":
    main()
