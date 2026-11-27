# -*- coding: utf-8 -*-
"""실행 계측기. workspace 의 프로그램을 감싸 실행하면서 '실제로 일어난 일' 을 Studio 로 보낸다.
(직접 쓰지 않는다. `python harness/run.py start workspace/app.py` 가 이걸로 서비스를 띄운다.)

수집하는 것 (모두 실제 관측값):
- HTTP 요청·응답 (표준 라이브러리 http.server 기반 서버): 메서드, 경로, 본문, 상태 코드, 응답 본문 일부
- workspace 안 코드의 함수 호출: 함수 이름, 파일·줄, 인자 값, 반환 값  (sys.setprofile)
- SQLite 쿼리 (sqlite3.connect 에 trace 콜백), workspace 안 파일 열기(읽기/쓰기)
- 프로세스 표준 출력/오류(실시간), 서버 주소, 종료 코드, 예외
한 HTTP 요청 안에서 일어난 일은 같은 trace id(T번호)로 묶는다.
비밀값으로 보이는 이름(password, token, secret, api_key, authorization, cookie)의 값은 *** 로 가린다.
"""
import os, sys, re, json, time, queue, runpy, threading, itertools, pathlib, traceback, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = pathlib.Path(os.environ.get("CS_HARNESS_RUN") or ROOT / ".harness")
WS = os.path.normcase(str((ROOT / "workspace").resolve()))
MAX_PER_TRACE = 300
SECRET = re.compile(r"pass(word)?|pwd|token|secret|api[_-]?key|authorization|cookie", re.I)


def studio_port():
    if os.environ.get("CS_STUDIO_PORT"):
        return os.environ["CS_STUDIO_PORT"]
    return (RUN / "port.txt").read_text(encoding="utf-8").strip()


BASE = f"http://127.0.0.1:{studio_port()}"
Q = queue.Queue()
_real_stdout, _real_stderr = sys.stdout, sys.stderr


def emit(type_, data, status="ok"):
    Q.put({"type": type_, "data": data, "status": status})


def _sender():
    while True:
        batch = [Q.get()]
        time.sleep(0.05)
        while not Q.empty() and len(batch) < 200:
            batch.append(Q.get_nowait())
        try:
            req = urllib.request.Request(BASE + "/batch", data=json.dumps({"events": batch}, ensure_ascii=False, default=str).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        except Exception as e:
            _real_stderr.write(f"[cstrace] Studio 전송 실패: {e}\n")


def flush(timeout=3):
    t = time.time()
    while not Q.empty() and time.time() - t < timeout:
        time.sleep(0.05)
    time.sleep(0.2)


def mask(v, name=""):
    if name and SECRET.search(str(name)):
        return "***"
    if isinstance(v, dict):
        return {k: ("***" if SECRET.search(str(k)) else mask(x)) for k, x in list(v.items())[:30]}
    if isinstance(v, (list, tuple)):
        return type(v)(mask(x) for x in list(v)[:30]) if isinstance(v, list) else tuple(mask(x) for x in list(v)[:30])
    return v


def srepr(v, name=""):
    try:
        r = repr(mask(v, name))
    except Exception:
        r = "<표시 불가>"
    return r if len(r) <= 300 else r[:300] + "…"


def body_text(raw):
    """HTTP 본문을 사람이 읽게: JSON 이면 비밀 키를 가리고 한글을 풀어 쓴다. 아니면 원문(비밀 키=값 형태는 가림)."""
    t = raw.decode("utf-8", "replace")
    try:
        return json.dumps(mask(json.loads(t)), ensure_ascii=False)[:2000]
    except Exception:
        return re.sub(r"((?:pass(?:word)?|pwd|token|secret|api[_-]?key)=)[^&\s]*", lambda m: m.group(1) + "***", t, flags=re.I)[:2000]


def tname(v):
    return type(v).__name__


# ---------- trace id ----------
local = threading.local()
_tseq = itertools.count(1)


def new_trace():
    local.trace = f"T{next(_tseq)}"
    local.count = 0
    return local.trace


def cur_trace():
    return getattr(local, "trace", None)


# ---------- 함수 호출 계측 ----------
_ws_cache = {}


def _in_ws(co):
    r = _ws_cache.get(co)
    if r is None:
        fn = co.co_filename
        if not fn or fn.startswith("<"):          # <frozen ntpath>, <string> 같은 내장·가상 코드는 내 코드가 아니다
            r = False
        else:
            f = os.path.normcase(os.path.abspath(fn))
            r = os.path.relpath(f, WS).replace("\\", "/") if f.startswith(WS + os.sep) and not co.co_name.startswith("<") else False
        _ws_cache[co] = r
    return r


def _profile(frame, event, arg):
    if event != "call" and event != "return":
        return
    co = frame.f_code
    rel = _in_ws(co)
    if not rel:
        return
    t = cur_trace()
    if t:
        local.count = getattr(local, "count", 0) + 1
        if local.count > MAX_PER_TRACE:
            return
    name = getattr(co, "co_qualname", co.co_name)
    if event == "call":
        n = co.co_argcount + co.co_kwonlyargcount + (1 if co.co_flags & 4 else 0) + (1 if co.co_flags & 8 else 0)
        args = {}
        for k in co.co_varnames[:n]:
            if k in ("self", "cls"):
                continue
            v = frame.f_locals.get(k)
            args[k] = {"value": srepr(v, k), "type": tname(v)}
        emit("CALL", {"trace": t, "fn": name, "file": rel, "line": co.co_firstlineno, "args": args})
    else:
        emit("RETURN", {"trace": t, "fn": name, "file": rel, "line": co.co_firstlineno, "value": srepr(arg), "type": tname(arg)})


# ---------- HTTP 계측 (http.server) ----------
class _Tap:
    def __init__(self, f, sink, limit=4096, on_data=None):
        self._f, self._sink, self._limit, self._on = f, sink, limit, on_data

    def _cap(self, b):
        if b and len(self._sink) < self._limit:
            self._sink.extend(b[: self._limit - len(self._sink)])
        if b and self._on:
            self._on()
        return b

    def read(self, *a):
        return self._cap(self._f.read(*a))

    def readline(self, *a):
        return self._cap(self._f.readline(*a))

    def write(self, b):
        bb = bytes(b)
        if len(self._sink) < self._limit:
            self._sink.extend(bb[: self._limit - len(self._sink)])
        return self._f.write(b)

    def __getattr__(self, k):
        return getattr(self._f, k)


def _patch_http():
    import http.server as hs
    H = hs.BaseHTTPRequestHandler
    o_handle, o_parse, o_send = H.handle_one_request, H.parse_request, H.send_response

    def handle_one_request(self):
        new_trace()
        self._cs = {"in": bytearray(), "out": bytearray(), "status": None, "t0": time.time(), "req": False}
        try:
            o_handle(self)
        finally:
            c = self._cs
            if c["req"]:
                raw = bytes(c["out"])
                head, _, body = raw.partition(b"\r\n\r\n")
                ctype = ""
                for line in head.split(b"\r\n"):
                    if line.lower().startswith(b"content-type:"):
                        ctype = line.split(b":", 1)[1].strip().decode("latin-1")
                text = body.decode("utf-8", "replace")
                emit("HTTP_RESPONSE", {"trace": cur_trace(), "status": c["status"], "content_type": ctype,
                                       "body": body_text(body) if ("json" in ctype or "text/plain" in ctype) else f"({ctype}, {len(body)}바이트)",
                                       "ms": round((time.time() - c["t0"]) * 1000, 1)},
                     "ok" if (c["status"] or 500) < 400 else "error")
            local.trace = None

    def parse_request(self):
        ok = o_parse(self)
        if ok and hasattr(self, "_cs"):
            self._cs["req"] = True
            cs = self._cs
            trace = cur_trace()
            n = int(self.headers.get("Content-Length") or 0)

            def on_body():          # 본문을 다 읽은 순간(처리 함수보다 먼저) 기록
                if not cs.get("body_sent") and len(cs["in"]) >= min(n, 4096):
                    cs["body_sent"] = True
                    emit("HTTP_BODY", {"trace": trace, "body": body_text(bytes(cs["in"]))})
            self.rfile = _Tap(self.rfile, cs["in"], 4096, on_body if n else None)
            self.wfile = _Tap(self.wfile, self._cs["out"], 8192)
            emit("HTTP_REQUEST", {"trace": cur_trace(), "method": self.command, "path": self.path,
                                  "client": self.client_address[0]})
        return ok

    def send_response(self, code, message=None):
        if hasattr(self, "_cs"):
            self._cs["status"] = code
        return o_send(self, code, message)

    H.handle_one_request, H.parse_request, H.send_response = handle_one_request, parse_request, send_response

    import socketserver
    o_bind = socketserver.TCPServer.server_bind

    def server_bind(self):
        o_bind(self)
        host, port = self.server_address[:2]
        url = f"http://127.0.0.1:{port}/"
        (RUN / "app_url.txt").write_text(url, encoding="utf-8")
        emit("APP_URL", {"url": url, "port": port})

    socketserver.TCPServer.server_bind = server_bind


# ---------- DB / 파일 계측 ----------
def _patch_db_files():
    import sqlite3, builtins
    o_connect = sqlite3.connect

    def connect(*a, **k):
        c = o_connect(*a, **k)
        db = str(a[0] if a else k.get("database", ""))
        c.set_trace_callback(lambda sql: emit("DB_QUERY", {"trace": cur_trace(), "db": os.path.basename(db), "sql": sql[:800]}))
        return c

    sqlite3.connect = connect
    o_open = builtins.open

    def open_(file, mode="r", *a, **k):
        f = o_open(file, mode, *a, **k)
        try:
            p = os.path.normcase(os.path.abspath(os.fspath(file)))
            if p.startswith(WS) and not p.endswith((".py", ".pyc")):
                emit("FILE_IO", {"trace": cur_trace(), "path": os.path.relpath(p, WS).replace("\\", "/"),
                                 "mode": "쓰기" if any(m in mode for m in "wax+") else "읽기"})
        except Exception:
            pass
        return f

    builtins.open = open_


# ---------- 표준 출력 ----------
class _Tee:
    def __init__(self, real, stream):
        self.real, self.stream, self.buf = real, stream, ""

    def write(self, s):
        self.real.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            emit("PROCESS_OUTPUT", {"stream": self.stream, "text": line[:1000], "trace": cur_trace()},
                 "error" if self.stream == "stderr" else "ok")
        return len(s)

    def flush(self):
        self.real.flush()

    def __getattr__(self, k):
        return getattr(self.real, k)


def main():
    if len(sys.argv) < 2:
        sys.exit("사용: python harness/cstrace.py <workspace 의 파이썬 파일> [인자…]")
    script = os.path.abspath(sys.argv[1])
    threading.Thread(target=_sender, daemon=True).start()
    _patch_http()
    _patch_db_files()
    sys.stdout, sys.stderr = _Tee(_real_stdout, "stdout"), _Tee(_real_stderr, "stderr")
    sys.argv = [script] + sys.argv[2:]
    sys.path.insert(0, os.path.dirname(script))
    emit("PROCESS_START", {"pid": os.getpid(), "script": os.path.relpath(script, WS).replace("\\", "/")})
    code = 0
    threading.setprofile(_profile)
    sys.setprofile(_profile)
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except KeyboardInterrupt:
        code = 130
    except BaseException:
        code = 1
        emit("ERROR", {"trace": cur_trace(), "traceback": traceback.format_exc()[-3000:]}, "error")
    finally:
        sys.setprofile(None)
        threading.setprofile(None)
        emit("PROCESS_EXIT", {"pid": os.getpid(), "code": code}, "ok" if code == 0 else "error")
        flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
