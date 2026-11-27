# -*- coding: utf-8 -*-
"""개발 문맥(목적 → 기능 → 문제 → 대안 → 선택 → 검증)을 Studio 에 기록한다.
모든 개발 이벤트는 기록 시점의 목적·기능에 연결된다. hook 은 목적과 기능이 없으면 코드 쓰기를 막는다.

사용 (Claude 가 실행):
  python harness/ctx.py service  "서비스 전체 목적"
  python harness/ctx.py goal     "지금 이루려는 목적"            ← 새 목적이면 아래 항목이 비워진다
  python harness/ctx.py feature  "목적에 필요한 기능"
  python harness/ctx.py success  "이 목적의 성공 조건(검증 기준)"
  python harness/ctx.py limit    "지금 상태(기존 방식)로 안 되는 점"
  python harness/ctx.py problem  "해결해야 하는 문제"
  python harness/ctx.py option   "후보 기술/방법" --pro "장점" --con "단점"
  python harness/ctx.py decide   "선택한 것(또는 '새 기술 도입 안 함')" --why "이유"
  python harness/ctx.py experiment "무엇을 비교/재현" --kind real|compare   (real=실제 문제, compare=교육용 비교 실험)
  python harness/ctx.py verify   pass|fail "처음 목적을 달성했는지와 근거"
  python harness/ctx.py show
"""
import json, os, sys, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")
KINDS = ("service", "goal", "feature", "success", "limit", "problem", "option", "decide", "experiment", "verify", "show")


def studio_port():
    if os.environ.get("CS_STUDIO_PORT"):
        return os.environ["CS_STUDIO_PORT"]
    try:
        return (RUN / "port.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def flag(args, name):
    if name in args:
        i = args.index(name)
        v = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        return v
    return ""


def main():
    args = sys.argv[1:]
    if not args or args[0] not in KINDS:
        sys.exit(__doc__)
    kind = args.pop(0)
    body = {"kind": kind}
    if kind == "option":
        body.update(pro=flag(args, "--pro"), con=flag(args, "--con"))
    elif kind == "decide":
        body.update(why=flag(args, "--why"))
    elif kind == "experiment":
        body.update(exp_kind=flag(args, "--kind") or "compare")
    elif kind == "verify":
        body.update(result=(args.pop(0) if args else "").lower())
        if body["result"] not in ("pass", "fail"):
            sys.exit("verify 는 pass 또는 fail 다음에 근거를 씁니다.")
    body["text"] = " ".join(args).strip()
    if kind != "show" and not body["text"]:
        sys.exit("내용이 비어 있습니다.")
    base = f"http://127.0.0.1:{studio_port()}"
    req = urllib.request.Request(base + "/context", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    ctx = json.loads(urllib.request.urlopen(req, timeout=3).read().decode("utf-8"))
    print(json.dumps(ctx, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
