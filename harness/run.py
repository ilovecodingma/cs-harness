# -*- coding: utf-8 -*-
"""서비스 실행기. workspace 의 프로그램을 실행 계측기(cstrace)로 감싸 백그라운드로 띄운다.
Studio 의 '실제 구현 화면', 흐름도 강조, 파라미터, 로그가 모두 여기서 나온 실제 값으로 그려진다.

  python harness/run.py start workspace/app.py [인자…]   서버형 프로그램 시작(이미 떠 있으면 끄고 다시)
  python harness/run.py once  workspace/tool.py [인자…]  한 번 실행하고 끝나는 프로그램(출력·종료 코드 그대로)
  python harness/run.py stop                             끄기
  python harness/run.py status
"""
import os, sys, json, time, signal, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")
WS = (ROOT / "workspace").resolve()
TRACE = ROOT / "harness" / "cstrace.py"
PIDF = RUN / "app.json"
URLF = RUN / "app_url.txt"


def ws_script(p):
    s = pathlib.Path(p)
    s = (ROOT / s) if not s.is_absolute() else s
    s = s.resolve()
    if WS not in s.parents:
        sys.exit(f"workspace 안의 파일만 실행합니다: {s}")
    if not s.exists():
        sys.exit(f"파일이 없습니다: {s}")
    return s


def alive(pid):
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop(quiet=False):
    if not PIDF.exists():
        if not quiet:
            print("실행 중인 서비스 없음")
        return
    info = json.loads(PIDF.read_text(encoding="utf-8"))
    pid = info["pid"]
    if alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"서비스 종료: {info['script']} (pid {pid})")
        _post({"type": "PROCESS_EXIT", "data": {"pid": pid, "code": None, "by": "run.py stop"}})
    PIDF.unlink()
    if URLF.exists():
        URLF.unlink()


def _post(ev):
    try:
        import urllib.request
        port = os.environ.get("CS_STUDIO_PORT") or (RUN / "port.txt").read_text().strip()
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/batch", data=json.dumps({"events": [ev]}).encode(),
                                                      headers={"Content-Type": "application/json"}), timeout=2).read()
    except Exception:
        pass


def start(args):
    s = ws_script(args[0])
    stop(quiet=True)
    log = open(RUN / "app.log", "w", encoding="utf-8")
    flags = (0x08000000 | 0x00000200) if os.name == "nt" else 0   # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
    p = subprocess.Popen([sys.executable, "-X", "utf8", str(TRACE), str(s)] + args[1:], cwd=s.parent,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"}, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    PIDF.write_text(json.dumps({"pid": p.pid, "script": s.relative_to(WS).as_posix(), "started": time.time()}), encoding="utf-8")
    for _ in range(100):
        if URLF.exists():
            print(f"서비스 시작: {s.relative_to(WS).as_posix()} (pid {p.pid})  주소 {URLF.read_text().strip()}")
            return 0
        if p.poll() is not None:
            print(f"서비스가 바로 종료됨 (종료 코드 {p.returncode}). 출력:")
            print(open(RUN / "app.log", encoding="utf-8", errors="replace").read()[-2000:])
            PIDF.unlink()
            return p.returncode or 1
        time.sleep(0.1)
    print(f"서비스는 떠 있지만(pid {p.pid}) 10초 안에 서버 주소가 나오지 않았습니다. 출력:")
    print(open(RUN / "app.log", encoding="utf-8", errors="replace").read()[-2000:])
    return 0


def once(args):
    s = ws_script(args[0])
    r = subprocess.run([sys.executable, "-X", "utf8", str(TRACE), str(s)] + args[1:], cwd=s.parent,
                       env={**os.environ, "PYTHONUNBUFFERED": "1"})
    print(f"[종료 코드 {r.returncode}]")
    return r.returncode


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    cmd = a.pop(0)
    if cmd == "start" and a:
        sys.exit(start(a))
    if cmd == "once" and a:
        sys.exit(once(a))
    if cmd == "stop":
        return stop()
    if cmd == "status":
        if PIDF.exists():
            info = json.loads(PIDF.read_text(encoding="utf-8"))
            print(("실행 중" if alive(info["pid"]) else "꺼짐"), info, URLF.read_text().strip() if URLF.exists() else "")
        else:
            print("실행 중인 서비스 없음")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
