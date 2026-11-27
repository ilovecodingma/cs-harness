# -*- coding: utf-8 -*-
"""시스템 구조를 Studio 흐름도에 선언한다. 구조는 기능이 필요로 할 때 하나씩 자란다.
실행 중 어느 구성 요소가 일하는지는 계측(cstrace)의 실제 관측으로 강조된다 — files 를 정확히 적어야 연결된다.

  python harness/arch.py node <id> "<이름>" --role "역할" --kind client|server|service|storage|db|ai|external --files a.py,b.py
  python harness/arch.py link <from> <to> "전달되는 데이터"
  python harness/arch.py unlink <from> <to>
  python harness/arch.py remove <id>
  python harness/arch.py show
"""
import json, os, sys, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")


def flag(a, name):
    if name in a:
        i = a.index(name)
        v = a[i + 1] if i + 1 < len(a) else ""
        del a[i:i + 2]
        return v
    return None


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    op = a.pop(0)
    if op == "node" and len(a) >= 2:
        files = flag(a, "--files")
        b = {"op": "node", "id": a[0], "label": a[1], "role": flag(a, "--role") or "", "kind": flag(a, "--kind") or "service",
             "files": [f.strip().replace("\\", "/").removeprefix("workspace/") for f in files.split(",")] if files else []}
    elif op == "link" and len(a) >= 2:
        b = {"op": "link", "from": a[0], "to": a[1], "data": " ".join(a[2:])}
    elif op == "unlink" and len(a) == 2:
        b = {"op": "unlink", "from": a[0], "to": a[1]}
    elif op == "remove" and a:
        b = {"op": "remove", "id": a[0]}
    elif op == "show":
        b = {"op": "show"}
    else:
        sys.exit(__doc__)
    port = os.environ.get("CS_STUDIO_PORT") or (RUN / "port.txt").read_text().strip()
    r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/arch", data=json.dumps(b, ensure_ascii=False).encode("utf-8"),
                                                      headers={"Content-Type": "application/json"}), timeout=3)
    print(json.dumps(json.loads(r.read().decode("utf-8")), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
