# -*- coding: utf-8 -*-
"""하네스 시작. Studio 서버를 켜고 브라우저를 연 뒤 Claude Code 를 이 폴더에서 연다.
사용: python harness/start.py [--no-claude] [--watch-input]"""
import os, sys, time, subprocess, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")

def studio_port():
    """포트는 Studio 가 실제로 잡은 번호(.harness/port.txt). 환경변수가 있으면 그것."""
    if os.environ.get("CS_STUDIO_PORT"):
        return os.environ["CS_STUDIO_PORT"]
    try:
        return (RUN / "port.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def url():
    return f"http://127.0.0.1:{studio_port()}/"


def alive():
    try:
        import json
        return json.loads(urllib.request.urlopen(url() + "state", timeout=1).read()).get("app") == "cs-studio"
    except Exception:
        return False


def main():
    watch = "--watch-input" in sys.argv
    if not alive():
        try:
            (RUN / "port.txt").unlink()      # 남아 있던 옛 번호가 다른 프로그램을 가리키지 않게
        except OSError:
            pass
        cmd = [sys.executable, str(ROOT / "studio" / "app.py")] + (["--watch-input"] if watch else [])
        try:
            import webview  # noqa: F401
        except ImportError:
            print("CS Studio 프로그램에 필요한 pywebview 를 설치합니다...", flush=True)
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pywebview"])
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(cmd, cwd=ROOT, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if alive():
                break
            time.sleep(0.2)
        else:
            sys.exit("Studio 서버가 켜지지 않았습니다.")
    print("CS Studio 실행 중:", url(), flush=True)
    if "--no-claude" in sys.argv:
        return
    os.chdir(ROOT)
    sys.exit(subprocess.call(["claude", "/시작"], shell=(os.name == "nt")))


if __name__ == "__main__":
    main()
