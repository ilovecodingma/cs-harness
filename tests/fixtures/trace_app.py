# 계측기 검증용 고정 서버(교육 시나리오 아님). 표준 라이브러리 http.server + sqlite3.
import json, sqlite3, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

DB = "trace_test.db"


def init():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS trips (id INTEGER PRIMARY KEY, destination TEXT, days INTEGER)")
    c.commit()
    c.close()


def validate(trip, password=None):
    if not trip.get("destination"):
        raise ValueError("destination 없음")
    return {"destination": trip["destination"].strip(), "days": int(trip.get("days", 1))}


def save_trip(trip):
    c = sqlite3.connect(DB)
    cur = c.execute("INSERT INTO trips (destination, days) VALUES (?, ?)", (trip["destination"], trip["days"]))
    c.commit()
    new_id = cur.lastrowid
    c.close()
    return new_id


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        trip = validate(body, password=body.get("password"))
        tid = save_trip(trip)
        out = json.dumps({"id": tid, **trip}, ensure_ascii=False).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    init()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    print("서버 시작", srv.server_address[1], flush=True)
    srv.serve_forever()
