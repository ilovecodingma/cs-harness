# -*- coding: utf-8 -*-
"""CS Studio 서버 (표준 라이브러리만 사용).
- 이벤트를 받아 파일(.harness/events.jsonl)에 쌓고, 화면(브라우저)에 SSE 로 흘려보낸다.
- 실행 상태(running/paused)와 속도를 들고 있고, hook 이 이를 조회해 Claude 의 도구 실행을 붙잡는다.
- 선택: OS 수준 입력 감지(키보드·마우스 사용 여부만 본다. 입력 내용은 읽지 않는다).
"""
import json, os, sys, time, threading, queue, argparse, pathlib, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")
WS = ROOT / "workspace"
STATIC = pathlib.Path(__file__).resolve().parent / "static"
PORT = int(os.environ.get("CS_STUDIO_PORT", "0"))   # 0 이면 운영체제가 빈 포트를 준다

lock = threading.Lock()
events = []
subs = []
state = {"mode": "running", "delay": 0.3, "speed": 1.0, "grace_until": 0.0, "reason": "", "step": "(없음)", "question": None, "qseq": 0}
seq = 0
acks = set()                       # 화면이 다 보여 준 PRESENT 이벤트 id
AUTO_ACK = os.environ.get("CS_AUTO_ACK") == "1"   # 시험용: 화면 없이 바로 확인 처리

# ---------- 개발 문맥: 목적 → 기능 → (한계·문제) → 대안 → 선택 → 검증 ----------
# 모든 이벤트에 그 순간의 목적·기능 id 를 붙여, 코드·명령·출력이 "무엇을 위해" 일어났는지 연결한다.
def empty_feature_part():
    return {"limits": [], "problems": [], "options": [], "decision": None, "experiments": [], "verify": None}


ctx = {"service": "", "goal": None, "feature": None, "success": "", "seq": 0, "history": [], **empty_feature_part()}

# ---------- 시스템 구조: Claude 가 선언한 구성 요소와 연결. 실행 중 강조는 실제 관측 이벤트로 ----------
arch = {"nodes": {}, "links": []}


def arch_save():
    json.dump(arch, open(RUN / "arch.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def arch_apply(b):
    op = b.get("op")
    with lock:
        if op == "node":
            n = arch["nodes"].get(b["id"], {})
            n.update({k: b[k] for k in ("label", "role", "kind", "files") if b.get(k) is not None})
            n.setdefault("added", now())
            arch["nodes"][b["id"]] = n
        elif op == "link":
            arch["links"] = [l for l in arch["links"] if not (l["from"] == b["from"] and l["to"] == b["to"])]
            arch["links"].append({"from": b["from"], "to": b["to"], "data": b.get("data", "")})
        elif op == "unlink":
            arch["links"] = [l for l in arch["links"] if not (l["from"] == b["from"] and l["to"] == b["to"])]
        elif op == "remove":
            arch["nodes"].pop(b["id"], None)
            arch["links"] = [l for l in arch["links"] if b["id"] not in (l["from"], l["to"])]
        snap = json.loads(json.dumps(arch))
    if op != "show":
        arch_save()
        add_event("ARCH", {k: v for k, v in b.items()})
    return snap


def ctx_save():
    json.dump(ctx, open(RUN / "context.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def ctx_load():
    p = RUN / "context.json"
    if p.exists():
        try:
            ctx.update(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass


def ctx_apply(b):
    """문맥 한 항목을 반영. 새 목적이면 기능 이하를, 새 기능이면 문제 이하를 비운다(지난 것은 history 에 남김)."""
    k, text = b.get("kind"), (b.get("text") or "").strip()
    with lock:
        if k == "service":
            ctx["service"] = text
        elif k == "goal":
            ctx["success"] = ""
            if ctx["goal"]:
                ctx["history"].append({"goal": ctx["goal"], "feature": ctx["feature"], "verify": ctx["verify"], "decision": ctx["decision"]})
            ctx["seq"] += 1
            ctx["goal"] = {"id": f"G{ctx['seq']}", "text": text}
            ctx["feature"] = None
            ctx.update(empty_feature_part())
        elif k == "feature":
            if ctx["feature"]:
                ctx["history"].append({"goal": ctx["goal"], "feature": ctx["feature"], "verify": ctx["verify"], "decision": ctx["decision"]})
            ctx["seq"] += 1
            ctx["feature"] = {"id": f"F{ctx['seq']}", "text": text}
            ctx.update(empty_feature_part())
        elif k == "limit":
            ctx["limits"].append(text)
        elif k == "problem":
            ctx["problems"].append(text)
        elif k == "option":
            ctx["options"].append({"text": text, "pro": b.get("pro", ""), "con": b.get("con", "")})
        elif k == "decide":
            ctx["decision"] = {"text": text, "why": b.get("why", "")}
        elif k == "experiment":
            ctx["experiments"].append({"text": text, "kind": b.get("exp_kind", "compare")})
        elif k == "success":
            ctx["success"] = text
        elif k == "verify":
            ctx["verify"] = {"result": b.get("result"), "text": text}
        snap = json.loads(json.dumps(ctx))
    if k != "show":
        ctx_save()
        add_event("CONTEXT", {"kind": k, "text": text, **{x: b[x] for x in ("pro", "con", "why", "exp_kind", "result") if b.get(x)}})
    return snap


def now():
    return time.time()


def publish(ev):
    for q in list(subs):
        q.put(ev)


def add_event(type_, data=None, step=None, status="ok"):
    global seq
    with lock:
        seq += 1
        ev = {"id": seq, "ts": now(), "type": type_, "step": step or state["step"], "status": status, "data": data or {},
              "ctx": {"goal": (ctx["goal"] or {}).get("id"), "feature": (ctx["feature"] or {}).get("id")}}
        events.append(ev)
        with open(RUN / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    publish(ev)
    if AUTO_ACK and type_ == "PRESENT":
        with lock:
            acks.add(ev["id"])
    return ev


def set_mode(mode, reason=""):
    with lock:
        changed = state["mode"] != mode
        if mode == "running":
            state["question"] = None
        state["mode"] = mode
        state["reason"] = reason
        if mode == "running":
            state["grace_until"] = now() + 3.0   # 재개 클릭 직후 입력으로 곧바로 다시 멈추지 않게
    if changed:
        add_event("RESUME" if mode == "running" else "PAUSE", {"reason": reason})
        save_state()


def save_state():
    with lock:
        json.dump({k: v for k, v in state.items() if k != "grace_until"}, open(RUN / "state.json", "w", encoding="utf-8"), ensure_ascii=False)


def safe_ws_path(rel):
    p = (WS / rel).resolve()
    if WS.resolve() not in p.parents and p != WS.resolve():
        return None
    return p


def tree():
    out = []
    for p in sorted(WS.rglob("*")):
        if ".git" in p.parts or "node_modules" in p.parts or "__pycache__" in p.parts:
            continue
        out.append({"path": p.relative_to(WS).as_posix(), "dir": p.is_dir()})
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:          # Windows 일부 도구는 cp949 로 보낸다
            return json.loads(raw.decode("cp949", errors="replace"))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            b = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/state":
            with lock:
                self._json({**state, "now": now(), "app": "cs-studio", "viewers": len(subs), "auto_ack": AUTO_ACK})
        elif u.path == "/ack":
            i = int(q.get("id", ["0"])[0])
            with lock:
                self._json({"acked": i in acks})
        elif u.path == "/context":
            with lock:
                self._json(ctx)
        elif u.path == "/arch":
            with lock:
                self._json(arch)
        elif u.path == "/tree":
            self._json(tree())
        elif u.path == "/file":
            p = safe_ws_path(q.get("path", [""])[0])
            if not p or not p.is_file():
                return self._json({"error": "없음"}, 404)
            self._json({"path": q["path"][0], "text": p.read_text(encoding="utf-8", errors="replace")})
        elif u.path == "/events":
            since = int(q.get("since", ["0"])[0])
            with lock:
                self._json([e for e in events if e["id"] > since])
        elif u.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            qq = queue.Queue()
            with lock:
                backlog = [{**e, "backlog": True, "acked": e["id"] in acks} for e in events]
                subs.append(qq)
            try:
                for e in backlog:
                    self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        e = qq.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with lock:
                    if qq in subs:
                        subs.remove(qq)
        else:
            self._json({"error": "404"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        b = self._body()
        if u.path == "/event":
            ev = add_event(b.get("type", "EVENT"), b.get("data"), b.get("step"), b.get("status", "ok"))
            self._json({"id": ev["id"]})
        elif u.path == "/ack":
            with lock:
                acks.add(int(b.get("id", 0)))
            self._json({"ok": True})
        elif u.path == "/context":
            self._json(ctx_apply(b))
        elif u.path == "/arch":
            self._json(arch_apply(b))
        elif u.path == "/batch":                      # 실행 계측(cstrace)이 모아서 보내는 실제 실행 이벤트
            ids = [add_event(e.get("type", "RUNTIME"), e.get("data"), None, e.get("status", "ok"))["id"] for e in (b.get("events") or [])]
            self._json({"ids": ids})
        elif u.path == "/ask":                        # Studio 에서 사용자가 질문 → 과외 모드
            with lock:
                state["qseq"] += 1
                q = {"id": f"Q{state['qseq']}", "text": b.get("text", ""), "selected": b.get("selected"), "ts": now()}
                state["question"] = q
                state["mode"] = "tutoring"
                state["reason"] = "질문 " + q["id"]
            add_event("QUESTION", q)
            save_state()
            self._json(q)
        elif u.path == "/question/take":              # hook 이 질문을 꺼내 Claude 에게 전달
            with lock:
                q = state["question"]
                state["question"] = None
            if q:
                add_event("QUESTION_DELIVERED", {"id": q["id"]})
            self._json({"question": q})
        elif u.path == "/pause":
            set_mode("paused", b.get("reason", "사용자"))
            self._json({"ok": True})
        elif u.path == "/resume":
            set_mode("running", "재개")
            self._json({"ok": True})
        elif u.path == "/speed":
            with lock:
                if "delay" in b:
                    state["delay"] = max(0.0, float(b["delay"]))
                if "speed" in b:
                    state["speed"] = min(4.0, max(0.25, float(b["speed"])))
            save_state()
            self._json({"ok": True})
        elif u.path == "/step":
            with lock:
                state["step"] = b.get("step", state["step"])
            add_event("STEP_STARTED", {"title": b.get("title", "")}, b.get("step"))
            save_state()
            self._json({"ok": True})
        else:
            self._json({"error": "404"}, 404)


def input_watcher():
    """키보드·마우스 사용 여부만 본다(GetLastInputInfo). 입력 내용은 읽지 않는다."""
    import ctypes

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LII()
    lii.cbSize = ctypes.sizeof(lii)
    last_tick = None
    while True:
        time.sleep(0.2)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        if last_tick is None:
            last_tick = lii.dwTime
        if lii.dwTime != last_tick:
            last_tick = lii.dwTime
            with lock:
                active = state["mode"] == "running" and now() > state["grace_until"]
            if active:
                set_mode("paused", "사용자 입력 감지")


def parent_watch(pid):
    """Studio 창(app.py)이 꺼지면 서버도 끈다. 창 없는 서버가 남아 hook 이 엉뚱한 서버를 보지 않게."""
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if not h:
        os._exit(0)
    k.WaitForSingleObject(h, 0xFFFFFFFF)
    os._exit(0)


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch-input", action="store_true", help="OS 수준 입력 감지 켜기")
    ap.add_argument("--open", action="store_true", help="브라우저 열기")
    a = ap.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    WS.mkdir(exist_ok=True)
    class Srv(ThreadingHTTPServer):
        allow_reuse_address = False          # Windows 에선 True 면 이미 쓰는 포트에 같이 붙는다

        def server_bind(self):
            import socket
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    try:
        srv = Srv(("127.0.0.1", PORT), H)
    except OSError as e:
        sys.exit(f"포트 {PORT} 를 쓸 수 없습니다(다른 프로그램이 사용 중): {e}")
    srv.daemon_threads = True
    PORT = srv.server_address[1]
    (RUN / "port.txt").write_text(str(PORT), encoding="utf-8")
    (RUN / "events.jsonl").write_text("", encoding="utf-8")
    ctx_load()                     # 목적·기능 문맥은 세션이 바뀌어도 이어진다
    if (RUN / "arch.json").exists():
        try:
            arch.update(json.load(open(RUN / "arch.json", encoding="utf-8")))
        except Exception:
            pass
    save_state()
    add_event("STUDIO_STARTED", {"port": PORT, "watch_input": a.watch_input})
    if a.watch_input:
        threading.Thread(target=input_watcher, daemon=True).start()
    if os.environ.get("CS_PARENT_PID") and os.name == "nt":
        threading.Thread(target=parent_watch, args=(int(os.environ["CS_PARENT_PID"]),), daemon=True).start()
    if a.open:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
    print(f"CS Studio http://127.0.0.1:{PORT}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
