# -*- coding: utf-8 -*-
"""CS Studio 데스크톱 프로그램.
- Studio 서버는 별도 프로세스로 띄운다(창이 멈춰도 실행 제어는 살아 있게).
- 그 화면을 독립 창으로 띄우고, 새 작업을 보여 줄 때 창을 앞으로 가져온다.
- 창을 닫으면 서버도 끈다 → hook 이 작업을 막는다(화면 없이 개발이 진행되지 않음).
사용: python studio/app.py [--x 1920 --y 0 --w 1600 --h 960] [--watch-input]"""
import os, sys, time, json, ctypes, argparse, pathlib, subprocess, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")
TITLE = "CS Studio"

try:
    import webview
except ImportError:
    sys.exit("pywebview 가 없습니다:  python -m pip install pywebview")


LAST = {}


def find_hwnd():
    """이 프로세스가 띄운, 화면에 보이는 'CS Studio' 창. (FindWindowW 는 같은 이름의 숨은 창을 돌려줄 수 있다)"""
    import ctypes.wintypes as W
    u = ctypes.windll.user32
    me, hit = os.getpid(), []

    def cb(h, _):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            pid = W.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if b.value == TITLE and pid.value == me:
                hit.append(h)
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)(cb), 0)
    return hit[0] if hit else None


def bring_to_front():
    """창을 다른 창들 위로 올린다. '잠깐 항상 위' 를 켰다 끄는 방식이라 전면 전환 제한에 걸리지 않고,
    키보드 포커스는 빼앗지 않는다(사용자가 터미널에 질문을 치는 중이어도 끊기지 않음)."""
    if os.name != "nt":
        return False
    u = ctypes.windll.user32
    h = find_hwnd()
    LAST["hwnd"] = h
    if not h:
        LAST["why"] = "창을 찾지 못함"
        return False
    if u.IsIconic(h):
        u.ShowWindow(h, 4)                  # SW_SHOWNOACTIVATE
    import ctypes.wintypes as W
    swp = u.SetWindowPos
    # 64비트에서 HWND_TOPMOST(-1) 를 32비트 정수로 넘기면 다른 값이 되므로 인자 형식을 정확히 준다
    swp.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    swp.restype = W.BOOL
    flags = 0x0001 | 0x0002 | 0x0010 | 0x0040   # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
    ok1 = swp(h, W.HWND(-1), 0, 0, 0, 0, flags)    # HWND_TOPMOST
    ok2 = swp(h, W.HWND(-2), 0, 0, 0, 0, flags)    # HWND_NOTOPMOST (맨 위에 둔 채로 '항상 위' 만 해제)
    LAST["why"] = f"SetWindowPos {ok1},{ok2}"
    return bool(ok1 and ok2)


def front_watcher(port):
    """새로 보여 줄 작업(PRESENT)이 오면 창을 앞으로. 화면(JS) 상태와 상관없이 프로그램이 직접 한다."""
    last = 0
    while True:
        try:
            evs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/events?since={last}", timeout=2).read())
            for e in evs:
                last = max(last, e["id"])
                d = e.get("data") or {}
                front = e["type"] in ("PRESENT", "ARCH", "APP_URL", "QUESTION", "ERROR", "STEP_STARTED") or (
                    e["type"] == "HTTP_RESPONSE" and not any(x in (d.get("content_type") or "") for x in ("html", "javascript", "css", "image", "icon")))
                if front:
                    ok = bring_to_front()
                    urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{port}/event", headers={"Content-Type": "application/json"},
                        data=json.dumps({"type": "FOCUS", "status": "ok" if ok else "error",
                                         "data": {"for": e["id"], **LAST}}).encode("utf-8")), timeout=2).read()
        except Exception:
            pass
        time.sleep(0.25)


class Api:
    def focus(self):
        bring_to_front()


def alive(port):
    try:
        st = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state", timeout=1).read())
        return st.get("app") == "cs-studio"
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--w", type=int, default=1600)
    ap.add_argument("--h", type=int, default=960)
    ap.add_argument("--watch-input", action="store_true")
    a = ap.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    pf = RUN / "port.txt"
    if pf.exists():
        pf.unlink()
    srv = subprocess.Popen([sys.executable, "-X", "utf8", str(HERE / "server.py")] + (["--watch-input"] if a.watch_input else []),
                           cwd=ROOT, env={**os.environ, "CS_HARNESS_RUN": str(RUN), "CS_PARENT_PID": str(os.getpid())},
                           creationflags=0x08000000 if os.name == "nt" else 0)   # CREATE_NO_WINDOW
    try:
        port = None
        for _ in range(100):
            if pf.exists() and alive(pf.read_text().strip()):
                port = pf.read_text().strip()
                break
            time.sleep(0.1)
        if not port:
            sys.exit("Studio 서버가 켜지지 않았습니다.")
        webview.create_window(TITLE, f"http://127.0.0.1:{port}/", js_api=Api(),
                              width=a.w, height=a.h, x=a.x, y=a.y, min_size=(900, 600))
        import threading
        threading.Thread(target=front_watcher, args=(port,), daemon=True).start()
        webview.start()
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
