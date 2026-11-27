# -*- coding: utf-8 -*-
"""실행 제어 검증. 시험용 포트에 Studio 를 따로 띄우고 hook 에 가짜 Claude 입력을 넣어 본다.
사용: python -X utf8 tests/check_gate.py"""
import json, os, sys, time, subprocess, pathlib, threading, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
import tempfile
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_gate_"))
ENV = {k: v for k, v in os.environ.items() if k != "CS_STUDIO_PORT"}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8")
BASE = None
results = []


def http(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=3).read().decode("utf-8"))


def hook(payload):
    t = time.time()
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "hook.py")], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", env=ENV, cwd=ROOT)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    dec = out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return dec, time.time() - t, out


def check(name, cond, detail=""):
    results.append((name, cond))
    print(("통과 " if cond else "실패 ") + name + (f"  ({detail})" if detail else ""), flush=True)


def pre(tool, inp):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp, "cwd": str(ROOT)}


def main():
    WS.mkdir(exist_ok=True)
    f_in = str(WS / "_gate_test.py")
    # 1. Studio 꺼짐 → 거부
    d, _, _ = hook(pre("Write", {"file_path": f_in, "content": "x"}))
    check("Studio 꺼져 있으면 작업 거부", d == "deny")
    srv = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "server.py")], env=ENV, cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        global BASE
        for _ in range(50):
            if (RUN / "port.txt").exists():
                break
            time.sleep(0.2)
        BASE = "http://127.0.0.1:" + (RUN / "port.txt").read_text().strip()
        check("Studio 가 빈 포트를 잡고 자기임을 밝힘", http("/state").get("app") == "cs-studio", BASE)
        http("/speed", {"delay": 1.0})
        # 2. 같은 포트 두 번째 서버 → 실패
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "studio" / "server.py")],
                           env={**ENV, "CS_STUDIO_PORT": BASE.rsplit(":", 1)[1], "CS_HARNESS_RUN": str(RUN / "dup")}, cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", timeout=10)
        check("같은 포트 중복 실행 막힘", r.returncode != 0)
        # 3. workspace 밖 → 거부
        d, _, _ = hook(pre("Write", {"file_path": str(ROOT / "evil.txt"), "content": "x"}))
        check("작업 공간 밖 파일 쓰기 거부", d == "deny")
        d, _, _ = hook(pre("Edit", {"file_path": str(ROOT / "CLAUDE.md"), "old_string": "a", "new_string": "b"}))
        check("작업 공간 밖 파일 수정 거부", d == "deny")
        d, _, _ = hook(pre("Agent", {"prompt": "다 만들어"}))
        check("하위 에이전트 같은 기타 도구는 거부", d == "deny")
        d, dt, _ = hook(pre("Read", {"file_path": str(ROOT / "CLAUDE.md")}))
        check("읽기 도구는 바로 통과", d == "allow" and dt < 1.0, f"{dt:.2f}초")
        http("/pause", {"reason": "검증0"})
        box0 = {}
        th0 = threading.Thread(target=lambda: box0.update(r=hook(pre("PowerShell", {"command": "Get-Date"}))))
        th0.start(); time.sleep(3)
        check("PowerShell 도 일시정지 중 붙잡힘", th0.is_alive())
        http("/resume", {}); th0.join(15)
        # 4. 정상 → 허용 + 지연
        d, dt, _ = hook(pre("Write", {"file_path": f_in, "content": "x"}))
        check("작업 공간 안 쓰기 허용 + 지연 적용", d == "allow" and dt >= 1.0, f"{dt:.2f}초")
        # 5. 일시정지 → 붙잡힘, 재개 → 진행
        http("/pause", {"reason": "검증"})
        box = {}
        th = threading.Thread(target=lambda: box.update(r=hook(pre("Bash", {"command": "echo hi"}))))
        th.start()
        time.sleep(4)
        check("일시정지 중 새 작업이 시작되지 않음", th.is_alive(), "4초 동안 대기 중")
        http("/resume", {})
        th.join(15)
        check("재개하면 붙잡혔던 작업이 진행됨", not th.is_alive() and box["r"][0] == "allow", f"{box.get('r', ('?', 0))[1]:.1f}초 걸림")
        # 6. 설명 명령은 정지 중에도 통과(지연 없음)
        http("/pause", {"reason": "검증2"})
        d, dt, _ = hook(pre("Bash", {"command": "python harness/say.py 설명"}))
        check("일시정지 중에도 설명(say) 은 바로 통과", d == "allow" and dt < 1.0, f"{dt:.2f}초")
        http("/resume", {})
        # 7. 완료 판정은 실제 결과로
        pathlib.Path(f_in).write_text("x", encoding="utf-8")
        hook({"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": f_in}, "tool_response": {}})
        hook({"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": str(WS / "_none.py")}, "tool_response": {}})
        ev = [e for e in http("/events") if e["type"] == "VALIDATE"]
        check("파일이 있으면 VALIDATE ok", any(e["status"] == "ok" and e["data"]["exists"] for e in ev))
        check("파일이 없으면 VALIDATE error", any(e["status"] == "error" and not e["data"]["exists"] for e in ev))
        # 8. 명령 출력 이벤트
        hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"},
              "tool_response": {"stdout": "hi\n", "stderr": "", "interrupted": False}})
        ev = [e for e in http("/events") if e["type"] == "COMMAND_OUTPUT"]
        check("명령 출력이 원본 그대로 기록됨", ev and ev[-1]["data"]["stdout"] == "hi\n")
        # 9. 한글 cp949 요청에도 서버 생존
        raw = urllib.request.Request(BASE + "/pause", data='{"reason":"한글"}'.encode("cp949"), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(raw, timeout=3).read()
        check("cp949 요청에도 서버가 응답", http("/state")["reason"] == "한글")
        print("\n이벤트 기록:")
        for e in http("/events"):
            print(" ", e["id"], e["type"], e["status"], json.dumps(e["data"], ensure_ascii=False)[:80])
    finally:
        srv.terminate()
        for p in (WS / "_gate_test.py",):
            if p.exists():
                p.unlink()
    n = sum(1 for _, c in results if c)
    print(f"\n{n}/{len(results)} 통과")
    sys.exit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
