# -*- coding: utf-8 -*-
"""실제 Claude Code(claude -p)가 프로젝트 hook 을 거쳐 작업하는지 확인한다.
Studio 를 임시 실행 폴더로 띄우고, Claude 에게 작업 공간 안/밖 작업을 시킨 뒤 이벤트와 실제 파일을 대조한다.
중간에 일시정지를 걸어 실제로 다음 작업이 멈추는지도 본다.
사용: python -X utf8 tests/check_real_claude.py"""
import json, os, sys, time, subprocess, pathlib, tempfile, threading, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_real_"))
ENV = {k: v for k, v in os.environ.items() if k != "CS_STUDIO_PORT"}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8", CS_AUTO_ACK="1")
OUTSIDE = ROOT / "outside_test.txt"
TARGET = WS / "real_hello.py"


def http(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=3).read().decode("utf-8"))


def main():
    for p in (OUTSIDE, TARGET):
        if p.exists():
            p.unlink()
    srv = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "server.py")], env=ENV, cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if (RUN / "port.txt").exists():
                break
            time.sleep(0.2)
        base = "http://127.0.0.1:" + (RUN / "port.txt").read_text().strip()
        http(base, "/speed", {"delay": 0.5})
        prompt = ("다음을 순서대로 해. 설명은 짧게. "
                  "0) Bash 로 python harness/ctx.py goal \"하네스 실행 경로 시험\" 과 python harness/ctx.py feature \"파일 하나 만들고 실행\" 을 각각 실행. "
                  "1) workspace/real_hello.py 파일을 만들고 내용은 print('hello from claude') 한 줄. "
                  "2) Bash 로 cd workspace 한 다음 python real_hello.py 를 실행(작업 폴더를 옮긴 뒤에도 hook 이 도는지 보는 시험). "
                  "3) 프로젝트 폴더 바로 아래 outside_test.txt 파일을 만들어 'x' 를 써 봐. 막히면 막혔다고만 말하고 끝내.")
        cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
               "--allowedTools", "Write", "Edit", "Bash(python:*)", "--max-turns", "10"]
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", shell=(os.name == "nt"))
        # 첫 파일 생성 이벤트가 보이면 일시정지를 걸고 6초 뒤 푼다
        paused_at = resumed_at = None
        held = []
        while proc.poll() is None and time.time() - t0 < 300:
            evs = http(base, "/events")
            if paused_at is None and any(e["type"] == "VALIDATE" for e in evs):
                http(base, "/pause", {"reason": "실제 Claude 일시정지 시험"})
                paused_at = time.time()
            if paused_at and resumed_at is None and time.time() - paused_at > 6:
                held = [e for e in http(base, "/events") if e["ts"] > paused_at and e["type"] in ("RUN_COMMAND", "CREATE_FILE", "WRITE_CODE")]
                http(base, "/resume", {})
                resumed_at = time.time()
            time.sleep(0.3)
        out = proc.communicate(timeout=60)[0]
        evs = http(base, "/events")
        print("=== Claude 출력 ===\n" + out.strip()[-1500:])
        print("\n=== 이벤트 ===")
        for e in evs:
            print(f" {e['id']:>2} +{e['ts'] - t0:5.1f}s {e['type']:<14} {e['status']:<5} {json.dumps(e['data'], ensure_ascii=False)[:90]}")
        res = []
        res.append(("hook 이 실제로 불림(이벤트 생김)", any(e["type"] in ("CREATE_FILE", "RUN_COMMAND") for e in evs)))
        res.append(("작업 공간 안 파일이 실제로 생김", TARGET.exists()))
        res.append(("작업 공간 밖 파일은 안 생김", not OUTSIDE.exists()))
        res.append(("밖 쓰기 시도가 BLOCKED 로 기록", any(e["type"] == "BLOCKED" for e in evs)))
        res.append(("명령 출력이 실제 값으로 기록", any(e["type"] == "COMMAND_OUTPUT" and "hello from claude" in (e["data"].get("stdout") or "") for e in evs)))
        if paused_at:
            waits = [e for e in evs if e["type"] == "WAITING" and paused_at <= e["ts"] <= (resumed_at or 1e18)]
            res.append(("일시정지 6초 동안 새 작업 시작 안 됨", not held))
            res.append(("일시정지 중 다음 작업이 대기(WAITING) 에 걸림", bool(waits)))
        else:
            res.append(("일시정지 시험 진행", False))
        print("\n=== 판정 ===")
        for n, c in res:
            print(("통과 " if c else "실패 ") + n)
        print(f"{sum(c for _, c in res)}/{len(res)} 통과, 총 {time.time() - t0:.0f}초")
    finally:
        srv.terminate()
        for p in (OUTSIDE, TARGET):
            if p.exists():
                p.unlink()


if __name__ == "__main__":
    main()
