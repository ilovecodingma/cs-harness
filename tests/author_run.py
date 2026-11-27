# -*- coding: utf-8 -*-
"""저자 모드 실제 개발: 여행 동선 서비스의 첫 기능을 실제 Studio 창 + 실제 Claude 로 개발한다.
도중에 (1) 서비스가 뜨면 실제 HTTP 요청을 보내 입력·처리·출력 시각화를 확인하고, (2) Studio 질문을 넣어 과외 흐름을 확인한다.
결과(workspace, 기록)는 지우지 않는다. 기록은 .harness/runs/<이름>/ 에 복사한다.
사용: python -X utf8 tests/author_run.py <캡처폴더>"""
import json, os, sys, time, shutil, subprocess, pathlib, tempfile, urllib.request
from PIL import ImageGrab

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"
RUN = pathlib.Path(tempfile.mkdtemp(prefix="cs_author_"))
SHOT = pathlib.Path(sys.argv[1]); SHOT.mkdir(parents=True, exist_ok=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("CS_STUDIO_PORT", "CS_AUTO_ACK")}
ENV.update(CS_HARNESS_RUN=str(RUN), PYTHONIOENCODING="utf-8")


def http(base, path, body=None):
    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=5).read().decode("utf-8"))


def shot(name):
    ImageGrab.grab(bbox=(1920, 0, 3840, 1080), all_screens=True).save(SHOT / f"{name}.png")


def main():
    app = subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "studio" / "app.py"), "--x", "1922", "--y", "0", "--w", "1916", "--h", "1040"],
                           env=ENV, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = {"steps": []}
    try:
        for _ in range(100):
            if (RUN / "port.txt").exists():
                break
            time.sleep(0.2)
        base = "http://127.0.0.1:" + (RUN / "port.txt").read_text().strip()
        for _ in range(60):
            if http(base, "/state").get("viewers", 0) > 0:
                break
            time.sleep(0.5)
        http(base, "/speed", {"speed": 2})
        http(base, "/step", {"step": "01", "title": "친구와 여행 일정을 공유"})
        prompt = ("CLAUDE.md 의 원칙과 흐름대로 진행해. 질문하지 말고 바로 진행해. 저자 모드 첫 구간이다. "
                  "서비스 목적: 여행 동선 서비스 — 여행 일정과 동선을 정리하고 친구와 함께 본다. "
                  "이번 구간 목적: 친구와 여행 일정을 공유하고 싶다. 이번 구간에서 만들 기능: 여행 일정 등록 및 조회(목적지, 일수, 인원). "
                  "workspace 는 비어 있다. 목적에서 기능을, 기능에서 기술을 도출하고, 구조는 arch.py 로 자라게 하고, 서비스는 run.py 로 띄워 "
                  "실제 구현 화면(브라우저에서 보이는 화면)과 실제 요청으로 동작을 확인한 뒤 목적 달성을 검증해. "
                  "이번 구간은 첫 기능 하나에 집중하고, 데이터 저장 방식은 지금 필요한 만큼만 판단해. 설명은 짧게. 끝나면 서비스는 켜 둔 채로 끝내.")
        cmd = ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--allowedTools", "Write", "Edit", "Bash(python:*)", "--max-turns", "80"]
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", shell=(os.name == "nt"))
        n, last, asked, posted, resumed_at = 0, 0, None, False, None
        while proc.poll() is None and time.time() - t0 < 1500:
            evs = http(base, "/events")
            types = [e["type"] for e in evs]
            if time.time() - last > 20 and n < 30:
                shot(f"s{n:02d}"); n += 1; last = time.time()
            # (2) 구조가 처음 생기면 그 구성 요소를 선택한 채 질문
            if asked is None and "ARCH" in types and "CREATE_FILE" in types:
                a = http(base, "/arch")
                nid = next(iter(a["nodes"]), None)
                sel = {"type": "구성 요소", "id": nid, "label": a["nodes"][nid]["label"], "role": a["nodes"][nid].get("role")} if nid else None
                q = http(base, "/ask", {"text": "이 구성 요소는 왜 필요한가요? 없으면 친구가 일정을 못 보나요?", "selected": sel})
                asked = (q["id"], time.time()); log["steps"].append(["질문", q, sel])
                shot("q_asked")
            if asked and resumed_at is None:
                after = [e for e in evs if e["ts"] > asked[1]]
                delivered = any(e["type"] == "QUESTION_DELIVERED" for e in after)
                answered = [e for e in after if e["type"] == "EXPLAIN" and delivered and e["id"] > next(x["id"] for x in after if x["type"] == "QUESTION_DELIVERED")]
                if answered:
                    time.sleep(3); shot("q_answered")
                    http(base, "/resume", {}); resumed_at = time.time()
                    log["steps"].append(["답변 후 재개", answered[0]["data"]["text"][:300]])
                elif time.time() - asked[1] > 240:
                    log["steps"].append(["답변 없음 240초"]); http(base, "/resume", {}); resumed_at = time.time()
            # (1) 서비스가 뜨고 API 가 있으면 실제 요청
            if not posted and (RUN / "app_url.txt").exists() and resumed_at and "VERIFY" not in types:
                pass
            time.sleep(1.5)
        out = proc.communicate(timeout=120)[0]
        shot("end_claude")
        evs = http(base, "/events")
        # 서비스가 떠 있으면, 사람이 쓰듯 실제 요청을 한 번 더 보내 시각화 재생을 관찰
        url = (RUN / "app_url.txt").read_text().strip() if (RUN / "app_url.txt").exists() else None
        probe = None
        if url:
            routes = [e["data"] for e in evs if e["type"] == "HTTP_REQUEST" and e["data"]["method"] == "POST"]
            if routes:
                path = routes[-1]["path"]
                body = [e["data"]["body"] for e in evs if e["type"] == "HTTP_BODY"]
                try:
                    sample = json.loads(body[-1]) if body else {}
                except Exception:
                    sample = {}
                sample.update({k: v for k, v in {"destination": "강릉", "days": 2, "people": 3}.items() if k in sample or not sample})
                try:
                    r = urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + path, data=json.dumps(sample, ensure_ascii=False).encode("utf-8"),
                                                                      headers={"Content-Type": "application/json"}), timeout=5)
                    probe = (r.status, r.read().decode("utf-8")[:300], path, sample)
                except Exception as e:
                    probe = ("오류", str(e), path, sample)
                for i in range(6):
                    time.sleep(1.6); shot(f"probe_{i}")
        evs = http(base, "/events")
        print("=== Claude ===\n" + out.strip()[-2500:])
        print("\n=== 질문 흐름 ===", json.dumps(log, ensure_ascii=False)[:1500])
        print("\n=== 사람 요청 ===", probe)
        kinds = {}
        for e in evs:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        print("\n=== 이벤트 종류 수 ===", kinds)
        print("\n=== 문맥 ===", json.dumps(http(base, "/context"), ensure_ascii=False)[:2500])
        print("\n=== 구조 ===", json.dumps(http(base, "/arch"), ensure_ascii=False)[:2000])
        dst = ROOT / ".harness" / "runs" / "author_01"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(RUN, dst)
        print("\n기록:", dst, "\n캡처:", SHOT, "\nworkspace:", [p.name for p in WS.iterdir()])
        print(f"걸린 시간 {time.time() - t0:.0f}초")
    finally:
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "harness" / "run.py"), "stop"], env=ENV, cwd=ROOT, capture_output=True)
        app.terminate()


if __name__ == "__main__":
    main()
