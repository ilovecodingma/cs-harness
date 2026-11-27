# -*- coding: utf-8 -*-
"""Claude 가 Studio 의 설명 영역에 말을 올린다.  사용: python harness/say.py "설명" [--step 단계ID]"""
import json, os, sys, pathlib, urllib.request

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


base = f"http://127.0.0.1:{studio_port()}"
args = sys.argv[1:]
step = None
if "--step" in args:
    i = args.index("--step")
    step = args[i + 1]
    del args[i:i + 2]
text = " ".join(args)
body = json.dumps({"type": "EXPLAIN", "step": step, "data": {"text": text}}, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(base + "/event", data=body, headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=3).read()
