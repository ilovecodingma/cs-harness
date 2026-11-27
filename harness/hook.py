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


def is_say(cmd):  # 설명 이벤트 전달용 명령은 지연·정지 대상에서 뺀다
    return "harness/say.py" in cmd.replace("\\", "/")


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
    # 일시정지면 풀릴 때까지 새 작업을 시작하지 않는다
    waited = 0.0
    announced = False
    while st["mode"] != "running" and waited < MAX_WAIT:
        if not announced:
            call("/event", {"type": "WAITING", "data": {"tool": tool, "why": "일시정지 중이라 다음 작업을 시작하지 않음"}})
            announced = True
        time.sleep(0.3)
        waited += 0.3
        st = call("/state")
    if st["mode"] != "running":
        deny("일시정지가 풀리지 않아 작업을 시작하지 못했습니다.")
    # 이벤트 종류 결정(실제 상태를 보고 정한다)
    data = {"tool": tool}
    if tool == "Write":
        t = target_path(inp)
        typ = "WRITE_CODE" if t.exists() else "CREATE_FILE"
        data.update(path=t.relative_to(WS).as_posix(), bytes=len(inp.get("content", "").encode("utf-8")))
    elif tool in ("Edit", "MultiEdit"):
        typ = "MODIFY_CODE"
        data.update(path=target_path(inp).relative_to(WS).as_posix() if in_ws(target_path(inp)) else str(target_path(inp)))
    elif tool in SHELL_TOOLS:
        typ = "RUN_COMMAND"
        data.update(command=inp.get("command", ""), cwd=p.get("cwd", ""))
    else:
        typ = "TOOL"
    call("/event", {"type": typ, "data": data})
    time.sleep(float(st.get("delay", 0)))   # 사람이 볼 시간(표시 속도). 실제 로그는 누락하지 않는다.


def post(p):
    tool, inp = p["tool_name"], p.get("tool_input", {})
    if tool in SHELL_TOOLS and is_say(inp.get("command", "")):
        return
    resp = p.get("tool_response") or {}
    if tool in FILE_TOOLS:
        t = target_path(inp)
        ok = t is not None and t.exists()   # 완료는 실제 결과 확인 뒤에만
        call("/event", {"type": "VALIDATE", "status": "ok" if ok else "error",
                        "data": {"what": "파일 존재", "path": t.relative_to(WS).as_posix() if in_ws(t) else str(t), "exists": ok,
                                 "size": t.stat().st_size if ok else 0}})
    elif tool in SHELL_TOOLS:
        if isinstance(resp, dict):
            out, err = resp.get("stdout", ""), resp.get("stderr", "")
            code = resp.get("exit_code", resp.get("returncode"))
            interrupted = resp.get("interrupted", False)
        else:
            out, err, code, interrupted = str(resp), "", None, False
        call("/event", {"type": "COMMAND_OUTPUT", "status": "error" if (err or interrupted) else "ok",
                        "data": {"stdout": out, "stderr": err, "exit_code": code, "raw_keys": sorted(resp) if isinstance(resp, dict) else None}})


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
