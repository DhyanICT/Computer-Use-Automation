from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .types import TargetSpec


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def find_chrome() -> str:
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    for name in ["google-chrome", "chromium", "chromium-browser", "msedge"]:
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Chrome or Edge was not found. Set CHROME_PATH to the browser executable.")


class MiniWebSocket:
    def __init__(self, url: str):
        self.url = url
        parsed = urllib.parse.urlparse(url)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        raw = socket.create_connection((self.host, self.port), timeout=10)
        if parsed.scheme == "wss":
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self.sock.settimeout(30)
        self._handshake()

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket handshake failed")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket handshake was rejected: {response[:120]!r}")

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        frame = bytearray([0x81])
        if len(payload) < 126:
            frame.append(0x80 | len(payload))
        elif len(payload) <= 0xFFFF:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", len(payload)))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", len(payload)))
        mask = os.urandom(4)
        frame.extend(mask)
        frame.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(frame)

    def recv_text(self) -> str:
        while True:
            first = self._read_exact(2)
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return payload.decode("utf-8")
            if opcode == 8:
                raise RuntimeError("WebSocket closed")
            if opcode == 9:
                self._send_pong(payload)

    def _send_pong(self, payload: bytes) -> None:
        frame = bytearray([0x8A, 0x80 | len(payload)])
        mask = os.urandom(4)
        frame.extend(mask)
        frame.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(frame)

    def _read_exact(self, length: int) -> bytes:
        data = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise RuntimeError("Socket closed")
            data += chunk
        return data

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class CDPConnection:
    def __init__(self, ws_url: str):
        self.ws = MiniWebSocket(ws_url)
        self.next_id = 1

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        self.ws.send_text(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = json.loads(self.ws.recv_text())
            if raw.get("id") != message_id:
                continue
            if "error" in raw:
                raise RuntimeError(f"CDP error for {method}: {raw['error']}")
            return raw.get("result", {})

    def close(self) -> None:
        self.ws.close()


class BrowserSession:
    def __init__(self, work_dir: Path, headless: bool = True):
        self.work_dir = work_dir
        self.headless = headless
        self.port = find_free_port()
        self.user_data_dir = Path(tempfile.mkdtemp(prefix="cua-chrome-", dir=str(work_dir)))
        self.proc: subprocess.Popen[bytes] | None = None
        self.cdp: CDPConnection | None = None

    def start(self) -> "BrowserSession":
        chrome = find_chrome()
        args = [
            chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-background-networking",
            "--disable-crash-reporter",
            "--disable-features=Crashpad",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
            "about:blank",
        ]
        if self.headless:
            args.insert(1, "--headless=new")
            args.insert(2, "--disable-gpu")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page_ws = self._wait_for_page_ws()
        self.cdp = CDPConnection(page_ws)
        for method in ["Page.enable", "Runtime.enable", "DOM.enable"]:
            self.cdp.call(method)
        self.cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False},
        )
        return self

    @property
    def debug_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _wait_for_page_ws(self) -> str:
        deadline = time.time() + 15
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=1) as response:
                    pages = json.loads(response.read().decode("utf-8"))
                for page in pages:
                    if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                        return page["webSocketDebuggerUrl"]
            except Exception as exc:  # noqa: BLE001 - polling until Chrome is ready
                last_error = exc
            time.sleep(0.2)
        raise RuntimeError(f"Chrome did not expose a debugging page: {last_error}")

    def navigate(self, url: str) -> None:
        self._conn.call("Page.navigate", {"url": url})
        self.wait_ready()

    def wait_ready(self, timeout: float = 10) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                ready = self.evaluate("document.readyState")
                if ready in {"interactive", "complete"}:
                    time.sleep(0.2)
                    return
            except RuntimeError:
                pass
            time.sleep(0.1)
        raise TimeoutError("Page did not become ready")

    def observe(self) -> dict[str, Any]:
        return self.evaluate(OBSERVE_SCRIPT)

    def page_text(self) -> str:
        return self.evaluate("document.body ? document.body.innerText : ''") or ""

    def is_text_present(self, text: str) -> bool:
        return text.lower() in self.page_text().lower()

    def click(self, target: TargetSpec) -> dict[str, Any]:
        resolved = self._find(target)
        x = resolved["x"] + resolved["width"] / 2
        y = resolved["y"] + resolved["height"] / 2
        for event_type in ["mouseMoved", "mousePressed", "mouseReleased"]:
            params: dict[str, Any] = {"type": event_type, "x": x, "y": y, "button": "left", "clickCount": 1}
            if event_type == "mousePressed":
                params["buttons"] = 1
            self._conn.call("Input.dispatchMouseEvent", params)
        time.sleep(0.35)
        self.wait_ready()
        return resolved

    def fill(self, target: TargetSpec, value: str) -> dict[str, Any]:
        resolved = self._find(target)
        css_json = json.dumps(resolved["css"])
        self.evaluate(
            f"""(function(){{
const el = document.querySelector({css_json});
if (!el) throw new Error('Element vanished before fill');
el.scrollIntoView({{block: 'center', inline: 'center'}});
el.focus();
if ('value' in el) el.value = '';
el.dispatchEvent(new Event('input', {{bubbles: true}}));
}})()"""
        )
        self._conn.call("Input.insertText", {"text": value})
        self.evaluate(
            f"""(function(){{
const el = document.querySelector({css_json});
if (el) {{
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
}}
}})()"""
        )
        return resolved

    def extract(self, target: TargetSpec) -> str:
        target_json = json.dumps(target.__dict__)
        return self.evaluate(f"(function(){{ const target = {target_json}; {EXTRACT_SCRIPT} }})()")

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self._conn.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        path.write_bytes(base64.b64decode(result["data"]))
        return path

    def dom_snapshot(self, path: Path, max_chars: int = 120_000) -> Path:
        html = self.evaluate("document.documentElement ? document.documentElement.outerHTML : ''") or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html[:max_chars], encoding="utf-8")
        return path

    def evaluate(self, expression: str) -> Any:
        result = self._conn.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True, "userGesture": True},
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"].get("text", "JavaScript evaluation failed")
            description = result["exceptionDetails"].get("exception", {}).get("description")
            raise RuntimeError(description or details)
        remote = result.get("result", {})
        if "value" in remote:
            return remote["value"]
        if remote.get("type") == "undefined":
            return None
        return remote.get("description")

    def close(self) -> None:
        if self.cdp:
            self.cdp.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.user_data_dir, ignore_errors=True)

    @property
    def _conn(self) -> CDPConnection:
        if not self.cdp:
            raise RuntimeError("Browser session is not started")
        return self.cdp

    def _find(self, target: TargetSpec) -> dict[str, Any]:
        target_json = json.dumps(target.__dict__)
        resolved = self.evaluate(f"(function(){{ const target = {target_json}; {FIND_SCRIPT} }})()")
        if not resolved:
            raise RuntimeError(f"Target not found: {target}")
        return resolved


OBSERVE_SCRIPT = r"""
(function() {
  function norm(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }
  function visible(el) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  }
  function cssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    while (el && el.nodeType === Node.ELEMENT_NODE && el !== document.documentElement) {
      let part = el.tagName.toLowerCase();
      const parent = el.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(child => child.tagName === el.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
      }
      parts.unshift(part);
      el = parent;
    }
    return parts.join(' > ');
  }
  function controlLabel(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return norm(aria);
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return norm(label.innerText);
    }
    const row = el.closest('tr');
    if (row && row.cells && row.cells.length > 1) return norm(row.cells[0].innerText);
    return norm(el.innerText || el.value || el.placeholder || el.name || el.title || el.type);
  }
  const nodes = Array.from(document.querySelectorAll('a,button,input,select,textarea,[role="button"]')).filter(visible);
  return {
    url: location.href,
    title: document.title,
    text: norm(document.body ? document.body.innerText : '').slice(0, 4000),
    controls: nodes.slice(0, 80).map((el, index) => {
      const rect = el.getBoundingClientRect();
      return {
        index,
        role: el.tagName.toLowerCase() === 'a' ? 'link' : (el.tagName.toLowerCase() === 'button' ? 'button' : (el.getAttribute('role') || el.tagName.toLowerCase())),
        label: controlLabel(el),
        text: norm(el.innerText || el.value || el.placeholder || ''),
        name: el.getAttribute('name') || '',
        css: cssPath(el),
        box: {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)}
      };
    })
  };
})()
"""


FIND_SCRIPT = r"""
function norm(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}
function lower(value) {
  return norm(value).toLowerCase();
}
function visible(el) {
  const style = window.getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
}
function cssPath(el) {
  if (el.id) return '#' + CSS.escape(el.id);
  const parts = [];
  while (el && el.nodeType === Node.ELEMENT_NODE && el !== document.documentElement) {
    let part = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (parent) {
      const same = Array.from(parent.children).filter(child => child.tagName === el.tagName);
      if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
    }
    parts.unshift(part);
    el = parent;
  }
  return parts.join(' > ');
}
function controlLabel(el) {
  const aria = el.getAttribute('aria-label');
  if (aria) return norm(aria);
  if (el.id) {
    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (label) return norm(label.innerText);
  }
  const row = el.closest('tr');
  if (row && row.cells && row.cells.length > 1) return norm(row.cells[0].innerText);
  return norm(el.innerText || el.value || el.placeholder || el.name || el.title || el.type);
}
function allControls() {
  return Array.from(document.querySelectorAll('a,button,input,select,textarea,[role="button"]')).filter(visible);
}
function describe(el) {
  el.scrollIntoView({block: 'center', inline: 'center'});
  const rect = el.getBoundingClientRect();
  return {
    tag: el.tagName.toLowerCase(),
    label: controlLabel(el),
    text: norm(el.innerText || el.value || el.placeholder || ''),
    css: cssPath(el),
    x: rect.x,
    y: rect.y,
    width: rect.width,
    height: rect.height
  };
}
function rowControlForLabel(label) {
  const wanted = lower(label);
  for (const row of Array.from(document.querySelectorAll('tr'))) {
    if (!visible(row)) continue;
    const rowText = lower(row.innerText);
    if (!rowText.includes(wanted)) continue;
    const control = row.querySelector('input,select,textarea,button,a,[role="button"]');
    if (control && visible(control)) return control;
  }
  return null;
}
let el = null;
if (target.css) {
  try {
    const candidate = document.querySelector(target.css);
    if (candidate && visible(candidate)) el = candidate;
  } catch (error) {}
}
if (!el && target.label) el = rowControlForLabel(target.label);
if (!el && target.label) {
  const wanted = lower(target.label);
  el = allControls().find(candidate => lower(controlLabel(candidate)).includes(wanted));
}
if (!el && target.text) {
  const wanted = lower(target.text);
  el = allControls().find(candidate => lower(candidate.innerText || candidate.value || candidate.placeholder || controlLabel(candidate)).includes(wanted));
}
if (!el && target.label) {
  const wanted = lower(target.label);
  el = Array.from(document.querySelectorAll('a,button,input,select,textarea')).find(candidate => visible(candidate) && lower(candidate.innerText || candidate.value || candidate.name).includes(wanted));
}
if (!el) return null;
return describe(el);
"""


EXTRACT_SCRIPT = r"""
function norm(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}
const extractor = target.extractor || {};
if (extractor.type === 'table_row_value' || target.strategy === 'near_text') {
  const label = (extractor.label || target.label || target.text || '').toLowerCase();
  for (const row of Array.from(document.querySelectorAll('tr'))) {
    const cells = Array.from(row.cells || []);
    if (cells.length < 2) continue;
    const first = norm(cells[0].innerText).toLowerCase();
    if (first.includes(label)) {
      return norm(cells.slice(1).map(cell => cell.innerText).join(' '));
    }
  }
}
if (target.css) {
  const el = document.querySelector(target.css);
  if (el) return norm(el.innerText || el.value || '');
}
if (target.text || target.label) {
  const wanted = (target.text || target.label).toLowerCase();
  const body = norm(document.body ? document.body.innerText : '');
  const index = body.toLowerCase().indexOf(wanted);
  if (index >= 0) return body.slice(index, index + 240);
}
return '';
"""
