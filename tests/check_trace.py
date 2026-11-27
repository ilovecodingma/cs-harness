# -*- coding: utf-8 -*-
"""실행 계측 검증: run.py 로 띄운 실제 서버에 실제 요청을 보내고, Studio 에 쌓인 이벤트를 대조한다.
사용: python -X utf8 tests/check_trace.py"""
import json, os, sys, time, shutil, subprocess, pathlib, tempfile, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_trace_"))
ENV = {k: v for k, v in os.environ.items() if k != "CS_STUDIO_PORT"}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8", CS_AUTO_ACK="1")
APP = WS / "_trace_app.py"
res = []


def check(n, c, d=""):
    res.append(c)
    print(("통과 " if c else "실패 ") + n + (f"  ({d})" if d else ""), flush=True)


def http(base, path, body=None):
    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=5).read().decode("utf-8"))


def main():
    shutil.copy(ROOT / "tests" / "fixtures" / "trace_app.py", APP)
    srv = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "server.py")], env=ENV, cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            if (RUN / "port.txt").exists():
                break
            time.sleep(0.2)
        base = "http://127.0.0.1:" + (RUN / "port.txt").read_text().strip()
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "start", "workspace/_trace_app.py"],
                           env=ENV, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        print(r.stdout.strip())
        url = (RUN / "app_url.txt").read_text().strip()
        check("run.py 가 서비스를 띄우고 실제 주소를 알려줌", r.returncode == 0 and url.startswith("http://127.0.0.1:"), url)
        req = urllib.request.Request(url + "trips", data=json.dumps({"destination": " 제주도 ", "days": 3, "password": "hunter2"}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=5)
        body = json.loads(resp.read().decode("utf-8"))
        check("실제 요청에 서버가 실제로 응답", resp.status == 201 and body["destination"] == "제주도", str(body))
        time.sleep(1.5)
        evs = http(base, "/events")
        rq = [e for e in evs if e["type"] == "HTTP_REQUEST" and e["data"]["method"] == "POST"]
        check("HTTP 요청 관측(메서드·경로)", bool(rq) and rq[-1]["data"]["path"] == "/trips")
        t = rq[-1]["data"]["trace"] if rq else None
        same = [e for e in evs if (e.get("data") or {}).get("trace") == t]
        kinds = [e["type"] for e in same]
        print("  trace", t, "→", kinds)
        calls = {e["data"]["fn"]: e for e in same if e["type"] == "CALL"}
        rets = {e["data"]["fn"]: e for e in same if e["type"] == "RETURN"}
        check("요청 본문 관측(한글 풀어 씀, 처리 함수보다 먼저)", any(e["type"] == "HTTP_BODY" and "제주도" in e["data"]["body"] for e in same) and kinds.index("HTTP_BODY") < kinds.index("RETURN"))
        v = calls.get("validate")
        check("함수 호출과 실제 인자 값 관측(같은 trace)", v is not None and "제주도" in v["data"]["args"]["trip"]["value"], v and json.dumps(v["data"]["args"], ensure_ascii=False)[:120])
        check("비밀값으로 보이는 인자는 가림", v is not None and v["data"]["args"]["password"]["value"] == "'***'" and "hunter2" not in json.dumps(evs, ensure_ascii=False))
        rv = rets.get("validate")
        check("반환 값 관측(변환 결과: 공백 제거)", rv is not None and "'제주도'" in rv["data"]["value"], rv and rv["data"]["value"])
        q = [e for e in same if e["type"] == "DB_QUERY" and "INSERT" in e["data"]["sql"]]
        check("DB 쿼리 관측(같은 trace)", bool(q), q and q[0]["data"]["sql"])
        hr = [e for e in same if e["type"] == "HTTP_RESPONSE"]
        check("HTTP 응답 관측(상태·본문)", bool(hr) and hr[0]["data"]["status"] == 201 and "제주도" in hr[0]["data"]["body"])
        order = [k for k in kinds if k in ("HTTP_REQUEST", "CALL", "DB_QUERY", "HTTP_RESPONSE")]
        check("실행 순서: 요청 → 함수 → DB → 응답", order and order[0] == "HTTP_REQUEST" and order[-1] == "HTTP_RESPONSE" and order.index("DB_QUERY") > order.index("CALL"))
        check("프로세스 출력 실시간 수집", any(e["type"] == "PROCESS_OUTPUT" and "서버 시작" in e["data"]["text"] for e in evs))
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "stop"], env=ENV, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        time.sleep(0.5)
        check("run.py stop 으로 종료", "서비스 종료" in r.stdout and any(e["type"] == "PROCESS_EXIT" for e in http(base, "/events")))
    finally:
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "stop"], env=ENV, cwd=ROOT, capture_output=True)
        srv.terminate()
        time.sleep(0.5)
        for f in (APP, WS / "trace_test.db"):
            try:
                f.unlink()
            except OSError:
                pass
    print(f"\n{sum(res)}/{len(res)} 통과")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
