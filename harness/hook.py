# -*- coding: utf-8 -*-
"""Claude Code Hook. Claude 의 도구 호출이 하네스를 거치게 만든다.
PreToolUse : 작업 직전. Studio 가 없으면 거부, 일시정지면 풀릴 때까지 붙잡고, 속도 지연을 둔 뒤 허용.
PostToolUse: 작업 직후. 실제 결과(파일 존재·명령 출력)를 확인해 이벤트로 올린다.
"""
import json, os, sys, time, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = (ROOT / "workspace").resolve()
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")

def studio_port():
    """포트는 Studio 가 실제로 잡은 번호(.harness/port.txt). 환경변수가 있으면 그것."""
    if os.environ.get("CS_STUDIO_PORT"):
        return os.environ["CS_STUDIO_PORT"]
    try:
        return (RUN / "port.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


BASE = f"http://127.0.0.1:{studio_port()}"
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
# 읽기만 하는 도구: Studio 가 꺼져 있어도, 일시정지 중에도 통과(질문에 답할 때 필요). 기록만 남긴다.
READ_TOOLS = ("Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "ToolSearch")
# 그 밖의 도구(하위 에이전트, MCP 등)는 하네스 밖에서 일을 몰아서 처리할 수 있어 기본으로 막는다.
MAX_WAIT = 3000  # 초. 일시정지 최대 대기
# 관찰 단위는 줄 수가 아니라 '의미 있는 변화'(함수·모델·API·저장 방식 …). 줄 수 상한은 대량 일괄 작성을 막는 보조 장치.
MAX_BLOCK = int(os.environ.get("CS_MAX_BLOCK_LINES", "60"))
CODE_EXT = (".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".sql", ".json", ".toml", ".yaml", ".yml")


def code_lines(text):
    return sum(1 for l in (text or "").splitlines() if l.strip())


def call(path, data=None, timeout=3):
    req = urllib.request.Request(BASE + path, data=None if data is None else json.dumps(data).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)


def target_path(inp):
    p = inp.get("file_path") or inp.get("notebook_path")
    return pathlib.Path(p).resolve() if p else None


def in_ws(p):
    return p is not None and (p == WS or WS in p.parents)


import re
_META = re.compile(r"^\s*(?:&\s*)?python(?:3)?(?:\.exe)?\s+(?:-X\s+utf8\s+)?(?:\./)?harness/(?:say|ctx|arch)\.py(?:\s|$)")


def is_say(cmd):
    """설명·문맥 기록용 명령(say.py/ctx.py)만 지연·정지·표시 없이 통과.
    명령 전체가 그 호출 하나일 때만 인정한다(뒤에 ; && | > 등으로 다른 명령을 붙이면 일반 명령으로 취급)."""
    c = cmd.replace("\\", "/")
    if not _META.match(c):
        return False
    # 따옴표 밖의 연결·치환 기호가 있으면 다른 명령이 섞인 것
    outside, q = [], None
    for ch in c:
        if q:
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
            continue
        outside.append(ch)
    rest = "".join(outside)
    return not any(t in rest for t in (";", "&&", "||", "|", ">", "<", "`", "$(", "\n"))


def pre(p):
    tool, inp = p["tool_name"], p.get("tool_input", {})
    if tool in READ_TOOLS:
        if tool == "Read":
            try:
                call("/event", {"type": "READ_FILE", "data": {"tool": tool, "path": inp.get("file_path", "")}})
            except Exception:
                pass
        return
    if tool not in FILE_TOOLS + SHELL_TOOLS:
        deny(f"이 하네스에서는 '{tool}' 도구를 쓰지 않습니다. 파일 도구와 Bash/PowerShell 만 하네스를 거쳐 실행합니다.")
    try:
        st = call("/state")
        assert st.get("app") == "cs-studio"
    except Exception:
        deny("CS Studio 가 실행 중이 아닙니다. 먼저 `python harness/start.py` 로 Studio 를 켜세요.")
    if tool in FILE_TOOLS:
        t = target_path(inp)
        if not in_ws(t):
            call("/event", {"type": "BLOCKED", "status": "error", "data": {"tool": tool, "path": str(t), "why": "실습 작업 공간(workspace) 밖"}})
            deny(f"실습 작업 공간(workspace/) 밖의 파일입니다: {t}")
    if tool in SHELL_TOOLS and is_say(inp.get("command", "")):
        return
    # Studio 에서 사용자가 질문했으면, 하려던 작업 대신 질문을 Claude 에게 전달한다(과외 모드)
    q = call("/question/take", {}).get("question")
    if q:
        sel = q.get("selected")
        nl = chr(10)
        deny(f"[사용자 질문 {q['id']}] {q['text']}"
             + (f"{nl}(사용자가 화면에서 선택한 요소: {json.dumps(sel, ensure_ascii=False)})" if sel else "")
             + nl + "하려던 작업은 아직 하지 않았습니다. 먼저 실제 파일·실행 결과·흐름을 확인해 `python harness/say.py` 로 답하세요. "
               "필요하면 실험해서 보여 주되 `python harness/ctx.py experiment ... --kind compare` 로 남기고, "
               "끝나면 어디로 돌아가는지 말한 뒤 원래 작업을 다시 시도하세요.")
    # 목적이 기능을, 기능이 기술(코드)을 결정한다: 현재 목적·기능이 정해지지 않으면 코드를 쓰지 않는다
    if tool in FILE_TOOLS:
        c = call("/context")
        if not c.get("goal") or not c.get("feature"):
            call("/event", {"type": "BLOCKED", "status": "error", "data": {"tool": tool, "why": "현재 목적·기능이 정해지지 않음"}})
            deny("지금 무엇을 위해 이 코드를 쓰는지(목적과 기능)가 정해지지 않았습니다. 먼저 "
                 "`python harness/ctx.py goal \"...\"` 와 `python harness/ctx.py feature \"...\"` 로 정하고, "
                 "기존 방식으로 안 되는 점과 선택 이유도 ctx.py 로 남긴 뒤 코드를 쓰세요.")
    # 블록 하나씩: 한 번에 너무 많은 코드를 쓰면 막는다(하나하나 보여 주고 설명하기 위해)
    if tool in FILE_TOOLS:
        if tool == "Write":
            t = target_path(inp)
            old = t.read_text(encoding="utf-8", errors="replace") if t.exists() else ""
            added = code_lines(inp.get("content", "")) - code_lines(old)
        else:
            eds = inp.get("edits") or [inp]
            added = sum(code_lines(e.get("new_string", "")) - code_lines(e.get("old_string", "")) for e in eds)
        if added > MAX_BLOCK:
            call("/event", {"type": "BLOCKED", "status": "error",
                            "data": {"tool": tool, "why": f"한 번에 코드 {added}줄 — 블록 하나({MAX_BLOCK}줄 이하)씩 써야 함"}})
            deny(f"한 번에 새 코드 {added}줄을 쓰려 했습니다. 이 하네스는 블록 하나({MAX_BLOCK}줄 이하)씩 쓰고, "
                 "쓰기 전에 say.py 로 그 블록이 왜 필요한지 설명합니다. 첫 블록을 Write 로, 다음 블록부터는 Edit 로 덧붙이세요.")
    # 일시정지면 풀릴 때까지 새 작업을 시작하지 않는다
    waited = 0.0
    announced = False
    while st["mode"] not in ("running", "tutoring") and waited < MAX_WAIT:
        if not announced:
            call("/event", {"type": "WAITING", "data": {"tool": tool, "why": "일시정지 중이라 다음 작업을 시작하지 않음"}})
            announced = True
        time.sleep(0.3)
        waited += 0.3
        st = call("/state")
    if st["mode"] not in ("running", "tutoring"):
        deny("일시정지가 풀리지 않아 작업을 시작하지 못했습니다.")
    explore = st["mode"] == "tutoring"
    # 이벤트 종류 결정(실제 상태를 보고 정한다)
    data = {"tool": tool}
    show = {"tool": tool}
    if tool == "Write":
        t = target_path(inp)
        typ = "WRITE_CODE" if t.exists() else "CREATE_FILE"
        rel = t.relative_to(WS).as_posix()
        data.update(path=rel, bytes=len(inp.get("content", "").encode("utf-8")))
        show.update(kind="write", path=rel, new_file=not t.exists(), content=inp.get("content", ""),
                    before=t.read_text(encoding="utf-8", errors="replace") if t.exists() else "")
    elif tool in ("Edit", "MultiEdit"):
        typ = "MODIFY_CODE"
        rel = target_path(inp).relative_to(WS).as_posix()
        data.update(path=rel)
        edits = inp.get("edits") or [{"old_string": inp.get("old_string", ""), "new_string": inp.get("new_string", "")}]
        show.update(kind="edit", path=rel, edits=edits)
    elif tool in SHELL_TOOLS:
        typ = "RUN_COMMAND"
        data.update(command=inp.get("command", ""), cwd=p.get("cwd", ""))
        show.update(kind="command", command=inp.get("command", ""), cwd=p.get("cwd", ""),
                    description=inp.get("description", ""))
    else:
        typ = "TOOL"
    if explore:                      # 과외 중 실험은 원래 개발 경로와 구분해 기록
        data["explore"] = show["explore"] = True
    # 실제로 하기 전에 화면에 먼저 보여 준다. 화면이 다 보여 줬다고 알려야(ack) 진행한다.
    ev = call("/event", {"type": "PRESENT", "data": show})
    data["present"] = ev["id"]       # 같은 작업의 화면 표시·실행·검증을 하나로 잇는 식별자
    waited = 0.0
    while waited < MAX_WAIT:
        st = call("/state")
        if st.get("viewers", 0) == 0 and not st.get("auto_ack"):
            call("/event", {"type": "BLOCKED", "status": "error", "data": {"tool": tool, "why": "Studio 화면이 열려 있지 않음"}})
            deny("CS Studio 화면이 열려 있지 않아 작업을 보여 줄 수 없습니다. `python harness/start.py` 로 Studio 를 여세요.")
        if call(f"/ack?id={ev['id']}").get("acked"):
            break
        time.sleep(0.3)
        waited += 0.3
    else:
        deny("화면이 작업을 다 보여 주지 못해 시작하지 않았습니다.")
    # 화면을 보는 동안 일시정지가 걸렸으면 풀릴 때까지 대기
    while call("/state")["mode"] not in ("running", "tutoring"):
        time.sleep(0.3)
    if tool in SHELL_TOOLS:          # 명령이 workspace 파일을 몰래 바꾸는지 보려고 실행 전 상태를 찍어 둔다
        snap_save(p.get("tool_use_id") or "last", snapshot())
    call("/event", {"type": typ, "data": data})
    time.sleep(float(st.get("delay", 0)))


def post(p):
    tool, inp = p["tool_name"], p.get("tool_input", {})
    if tool in SHELL_TOOLS and is_say(inp.get("command", "")):
        return
    resp = p.get("tool_response") or {}
    if tool in FILE_TOOLS:
        # 완료 판정은 파일 존재가 아니라 '의도한 내용이 실제로 들어갔는가'
        t = target_path(inp)
        ok = t is not None and t.exists()
        text = t.read_text(encoding="utf-8", errors="replace") if ok else ""
        norm = lambda s: (s or "").replace(chr(13) + chr(10), chr(10))
        if tool == "Write":
            match = ok and norm(text) == norm(inp.get("content"))
            what = "파일 내용 = 쓰려던 내용"
        else:
            eds = inp.get("edits") or [inp]
            match = ok and all(norm(e.get("new_string")) in norm(text) for e in eds)
            what = "수정한 내용이 파일에 들어감"
        call("/event", {"type": "VALIDATE", "status": "ok" if match else "error",
                        "data": {"what": what, "path": t.relative_to(WS).as_posix() if in_ws(t) else str(t), "exists": ok,
                                 "content_ok": bool(match), "size": t.stat().st_size if ok else 0}})
    elif tool in SHELL_TOOLS:
        if isinstance(resp, dict):
            out, err = resp.get("stdout", ""), resp.get("stderr", "")
            code = resp.get("exit_code", resp.get("returncode"))
            interrupted = resp.get("interrupted", False)
        else:
            out, err, code, interrupted = str(resp), "", None, False
        call("/event", {"type": "COMMAND_OUTPUT", "status": "error" if (err or interrupted) else "ok",
                        "data": {"stdout": out, "stderr": err, "exit_code": code, "raw_keys": sorted(resp) if isinstance(resp, dict) else None}})
        before = snap_load(p.get("tool_use_id") or "last")
        if before is not None:
            after = snapshot()
            added = sorted(set(after) - set(before))
            removed = sorted(set(before) - set(after))
            changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
            if added or removed or changed:
                code_touched = [f for f in added + changed + removed if f.endswith(CODE_EXT) and not f.endswith(".json")]
                call("/event", {"type": "FILES_CHANGED_BY_COMMAND", "status": "error" if code_touched else "ok",
                                "data": {"added": added, "changed": changed, "removed": removed, "code_touched": code_touched,
                                         "why": ("명령이 코드 파일을 바꿈 — 화면에 먼저 보이지 않은 변경" if code_touched else "프로그램 실행이 데이터 파일을 바꿈")}})


def snapshot():
    out = {}
    for f in WS.rglob("*"):
        if f.is_file() and not any(x in f.parts for x in (".git", "node_modules", "__pycache__", ".venv")):
            st = f.stat()
            out[f.relative_to(WS).as_posix()] = [st.st_size, int(st.st_mtime_ns)]
    return out


def snap_save(key, snap):
    (RUN / "snaps").mkdir(parents=True, exist_ok=True)
    (RUN / "snaps" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', key)}.json").write_text(json.dumps(snap), encoding="utf-8")


def snap_load(key):
    f = RUN / "snaps" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', key)}.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    f.unlink()
    return d


def main():
    p = json.load(sys.stdin)
    try:
        {"PreToolUse": pre, "PostToolUse": post}.get(p.get("hook_event_name"), lambda _: None)(p)
    except SystemExit:
        raise
    except Exception as e:
        if p.get("hook_event_name") == "PreToolUse":
            deny(f"하네스 hook 오류로 작업을 막았습니다: {e}")
        # PostToolUse 오류는 작업을 되돌릴 수 없으니 stderr 에만 남긴다
        print(f"hook 오류: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
