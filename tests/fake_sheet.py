#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Імітація веб-застосунку Apps Script (Code.gs v2) для тестів додатка без Google.
Запуск: python3 fake_sheet.py PORT [VERSION]   ->  http://127.0.0.1:PORT/exec
Стан (рядки «Журналу») — у пам'яті; GET /state віддає його для перевірок у тестах."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "test-token"
VERSION = int(sys.argv[2]) if len(sys.argv) > 2 else 2

# рядки аркуша «Журнал»: row -> 20 значень (A..T); перші 5 угод як у справжньому журналі
ROWS = {}
SEED = [
    [1, "2026-10-17", "Zcash", "Short", 1.231, 1.259, 1.128, 3.73, "так", 3.73, "Без змін", "ні", "ОБ + структура", 3.73, 0, 0, "", "", "", ""],
    [2, "2026-10-18", "Cardano", "Long", 0.223, 0.2175, 0.2361, 2.38, "ні", -2.2, "Без змін", "ні", "ранній вхід", 1.53, 2.2, 1, "", "", "", ""],
    [3, "2026-10-18", "Solana", "Long", 102, 101, 106, 4, "ні", -1.1, "Без змін", "ні", "3-й дотик", 0.43, 3.3, 2, "", "", "", ""],
    [4, "2026-10-20", "Solana", "Long", 104, 103, 107, 3, "так", 4.5, "Без змін", "так", "CHoCH від OB", 4.93, 0, 0, "", "", "", ""],
    [5, "2026-11-03", "TRUMP", "Long", 1.568, 1.544, 1.671, 4.29, "ні", -0.5, "Без змін", "ні", "Ризик 0,5R, тому стоп = -0,5R", 4.43, 0.5, 1, "4h OB", "ні", 2, "так"],
]
for v in SEED:
    ROWS[v[0] + 2] = v
CALLS = []
DOWN = {"on": False}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _out(self, o):
        b = json.dumps(o, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _down(self):
        b = b"<html><body>Google: service unavailable</body></html>"
        self.send_response(503)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/toggle"):
            DOWN["on"] = "down=1" in self.path
            return self._out({"down": DOWN["on"]})
        if DOWN["on"]:
            return self._down()
        if self.path.startswith("/state"):
            return self._out({"rows": ROWS, "calls": CALLS})
        if VERSION >= 2:
            return self._out({"ok": True, "version": VERSION})
        return self._out({"ok": True, "hint": "Тільки POST зі скрінера"})

    def do_POST(self):
        if DOWN["on"]:
            return self._down()
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        CALLS.append(d.get("action") or "screener")
        if d.get("token") != TOKEN:
            return self._out({"ok": False, "error": "bad token"})
        a = d.get("action")
        if a == "listTrades":
            return self._out({"ok": True, "trades": [{"row": r, "values": ROWS[r]} for r in sorted(ROWS) if ROWS[r][2]]})
        if a == "saveTrade":
            t = d["trade"]
            row = t["n"] + 2
            cur = ROWS.get(row)
            if cur and cur[2] and not t.get("overwrite") and str(cur[2]).upper() != str(t["instrument"]).upper():
                return self._out({"ok": False, "code": "occupied", "error": f"рядок {row} уже зайнятий: {cur[2]}"})
            if t["n"] > 1 and not (ROWS.get(row - 1) or [None] * 3)[2]:
                return self._out({"ok": False, "code": "gap", "error": "попередня угода порожня"})
            v = lambda k: "" if t.get(k) is None else t.get(k)
            ROWS[row] = [t["n"], v("date"), v("instrument"), v("dir"), v("entry"), v("stop"), v("target"), "", v("rules"), v("result"),
                         v("management"), v("choch"), v("note"), "", "", "", v("zone_type"), v("support4h"), v("mfe"), v("by_screener")]
            return self._out({"ok": True, "n": t["n"], "row": row})
        if a == "clearTrade":
            row = int(d["n"]) + 2
            if ROWS.get(row + 1) and ROWS[row + 1][2]:
                return self._out({"ok": False, "error": "видаляти можна лише останню угоду"})
            ROWS.pop(row, None)
            return self._out({"ok": True})
        return self._out({"ok": False, "error": "невідома дія"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    srv.serve_forever()
