from __future__ import annotations

import html
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MEMBERS: dict[str, dict[str, Any]] = {
    "12345": {
        "name": "Avery Johnson",
        "status": "Active",
        "savings_balance": "$4,218.52",
        "checking_balance": "$830.10",
    },
    "24680": {
        "name": "Morgan Lee",
        "status": "Active",
        "savings_balance": "$92.17",
        "checking_balance": "$1,410.66",
    },
    "77777": {
        "name": "Restricted Member",
        "status": "Permission Required",
        "savings_balance": "$0.00",
        "checking_balance": "$0.00",
    },
}


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LegacyCoreHandler(BaseHTTPRequestHandler):
    server_version = "LegacyCoreDemo/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/":
            self._send_html(self._layout("Operator Home", self._home()))
        elif path == "/members":
            self._send_html(self._layout("Member Servicing", self._lookup()))
        elif path == "/members/search":
            self._send_html(self._layout("Member Search", self._search(query)))
        elif path.startswith("/members/") and path.endswith("/freeze"):
            member_id = path.split("/")[2]
            self._send_html(self._layout("Freeze Card", self._freeze(member_id)))
        elif path.startswith("/members/"):
            member_id = path.split("/")[2]
            self._send_html(self._layout("Member Profile", self._profile(member_id, query)))
        else:
            self.send_error(404, "No route")

    def _send_html(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _layout(self, title: str, body: str) -> str:
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)} - Fiserv Classic Console</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f4f1e8; color: #1b2633; }}
    .chrome {{ background: #214f4b; color: white; padding: 10px 18px; font-size: 18px; }}
    .bar {{ background: #d8ddd4; padding: 7px 18px; border-bottom: 1px solid #9ba59e; }}
    .wrap {{ padding: 18px; }}
    table.legacy {{ border-collapse: collapse; background: white; min-width: 720px; border: 1px solid #7f8a86; }}
    table.legacy td, table.legacy th {{ border: 1px solid #7f8a86; padding: 8px 10px; }}
    table.legacy th {{ background: #e1e9e6; text-align: left; }}
    .prompt {{ background: #edf2ef; width: 230px; font-weight: bold; }}
    input {{ font-size: 15px; padding: 6px; width: 260px; }}
    button, .buttonish {{ font-size: 14px; padding: 7px 12px; border: 1px solid #57645f; background: #f6f7f2; color: #10251f; cursor: pointer; text-decoration: none; }}
    .notice {{ border: 2px solid #8b6b00; background: #fff8d5; padding: 10px; margin: 0 0 14px 0; max-width: 720px; }}
    .error {{ border: 2px solid #a33535; background: #fff0f0; padding: 10px; margin: 0 0 14px 0; max-width: 720px; }}
    .muted {{ color: #66716d; }}
  </style>
</head>
<body>
  <div class="chrome">Fiserv Classic Console <span class="muted">tenant: demo-credit-union</span></div>
  <div class="bar">Mainframe Bridge / No API Mode / Operator: training-user</div>
  <div class="wrap">{body}</div>
</body>
</html>"""

    def _home(self) -> str:
        return """<h1>Operations</h1>
<table class="legacy">
  <tr><th>Module</th><th>Action</th></tr>
  <tr><td>Member Servicing</td><td><a class="buttonish" href="/members">Member Servicing</a></td></tr>
  <tr><td>Card Operations</td><td><button disabled>Card Batch Queue</button></td></tr>
</table>"""

    def _lookup(self) -> str:
        return """<h1>Member Lookup</h1>
<form action="/members/search" method="get">
  <table class="legacy">
    <tr><td class="prompt">Member / Account Number</td><td><input name="member_id" autocomplete="off"></td></tr>
    <tr><td class="prompt">Search Type</td><td><select name="kind"><option>Member</option><option>Account</option></select></td></tr>
    <tr><td></td><td><button type="submit">Search</button></td></tr>
  </table>
</form>"""

    def _search(self, query: dict[str, list[str]]) -> str:
        member_id = query.get("member_id", [""])[0].strip()
        if not member_id.isdigit():
            return """<h1>Member Lookup</h1>
<div class="error">Validation error: member number must contain only digits.</div>""" + self._lookup()
        if member_id == "77777":
            return """<h1>Member Search Results</h1>
<div class="error">Permission denied: additional approval required to view this member.</div>"""
        member = MEMBERS.get(member_id)
        if not member:
            return f"""<h1>Member Search Results</h1>
<div class="notice">No member found for search value {html.escape(member_id)}.</div>
<p><a class="buttonish" href="/members">Back to lookup</a></p>"""
        return f"""<h1>Member Search Results</h1>
<table class="legacy">
  <tr><th>Member Number</th><th>Name</th><th>Status</th><th>Open</th></tr>
  <tr>
    <td>{html.escape(member_id)}</td>
    <td>{html.escape(member["name"])}</td>
    <td>{html.escape(member["status"])}</td>
    <td><a class="buttonish" href="/members/{html.escape(member_id)}">Open Profile</a></td>
  </tr>
</table>"""

    def _profile(self, member_id: str, query: dict[str, list[str]]) -> str:
        member = MEMBERS.get(member_id)
        if not member:
            return "<h1>Member Profile</h1><div class=\"notice\">No member found.</div>"
        notice = ""
        if query.get("notice", ["0"])[0] == "1":
            notice = "<div class=\"notice\">Transient host notice: nightly balance batch is complete. <button>Continue</button></div>"
        return f"""<h1>Member Profile</h1>
{notice}
<table class="legacy">
  <tr><td class="prompt">Member Number</td><td>{html.escape(member_id)}</td></tr>
  <tr><td class="prompt">Member Name</td><td>{html.escape(member["name"])}</td></tr>
  <tr><td class="prompt">Status</td><td>{html.escape(member["status"])}</td></tr>
  <tr><td class="prompt">Savings Balance</td><td>{html.escape(member["savings_balance"])}</td></tr>
  <tr><td class="prompt">Checking Balance</td><td>{html.escape(member["checking_balance"])}</td></tr>
</table>
<p><a class="buttonish" href="/members/{html.escape(member_id)}/freeze">Freeze Card</a></p>"""

    def _freeze(self, member_id: str) -> str:
        return f"""<h1>Freeze Card</h1>
<div class="error">Risky operation: freezing a card is not reversible without supervisor review.</div>
<table class="legacy">
  <tr><td class="prompt">Member Number</td><td>{html.escape(member_id)}</td></tr>
  <tr><td class="prompt">Action</td><td>Freeze debit card ending in 4412</td></tr>
</table>
<p><button>Confirm Freeze</button> <a class="buttonish" href="/members/{html.escape(member_id)}">Cancel</a></p>"""


class TargetServer:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None):
        self.host = host
        self.port = port or find_free_port()
        self.httpd = QuietThreadingHTTPServer((self.host, self.port), LegacyCoreHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "TargetServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        return
