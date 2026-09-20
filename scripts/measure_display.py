"""What a viewer browser actually paints from a live stream, and which transport gets there.

Two real browsers: one streams, one views through the display path a viewer uses.
Only DISTINCT frames are counted — setting the same image twice still fires a load
event, so counting loads alone would report a still picture as movement.

The sweep is the useful part. If a tighter poll keeps raising the painted rate then
the round trip is the limit and a push channel would help; if the rate is flat, the
limit is frame production and decode, and no transport can beat it.

    uv run python scripts/measure_display.py

No model calls, no network beyond loopback.
"""

import base64
import hashlib
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from jev_ultrafast.browser import Browser

SECONDS = 4.0
POLL_INTERVALS = (0, 4, 8, 16)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

SOURCE = """<!doctype html><title>source</title>
<div id="box" style="position:fixed;left:100px;top:100px;width:300px;height:300px;background:red"></div>
<script>
  const box = document.getElementById('box');
  let n = 0;
  const step = () => {
    n += 1;
    box.style.transform = 'translateX(' + (n % 700) + 'px)';
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
</script>"""

POLL_VIEWER = """<!doctype html><title>poll viewer</title>
<style>body{margin:0}img{width:1120px;height:780px;display:block}</style>
<img id="s">
<script>
  const MS = Number(new URLSearchParams(location.search).get('ms') || 16);
  let painted = 0, delivered = 0, last = null;
  const img = document.getElementById('s');
  img.addEventListener('load', () => { painted += 1; });
  async function poll() {
    try {
      const response = await fetch('/api/screen');
      if (response.ok) {
        const view = await response.json();
        if (view.screenshot && view.screenshot !== last) {
          last = view.screenshot;
          delivered += 1;
          img.src = 'data:image/jpeg;base64,' + view.screenshot;
        }
      }
    } catch (error) { /* a missed frame is not worth reporting */ }
    setTimeout(poll, MS);
  }
  poll();
  window.__stats = () => ({ painted, delivered, mode: 'poll ' + MS + 'ms' });
  window.__release = () => {};
</script>"""

WS_VIEWER = """<!doctype html><title>websocket viewer</title>
<style>body{margin:0}img{width:1120px;height:780px;display:block}</style>
<img id="s">
<script>
  let painted = 0, delivered = 0;
  const urls = [];
  const img = document.getElementById('s');
  img.addEventListener('load', () => { painted += 1; });
  const socket = new WebSocket('ws://127.0.0.1:__PORT__/ws');
  socket.binaryType = 'arraybuffer';
  // The server only sends a frame when it differs from the last one it sent, so
  // every message is already a distinct frame. A message counts as delivered when
  // it arrives and as painted when the image has actually decoded and loaded --
  // the two differ if the viewer cannot keep up.
  socket.onmessage = (event) => {
    const url = URL.createObjectURL(new Blob([event.data], { type: 'image/jpeg' }));
    urls.push(url);
    delivered += 1;
    img.src = url;
  };
  window.__stats = () => ({ painted, delivered, mode: 'websocket' });
  window.__release = () => { urls.forEach(URL.revokeObjectURL); urls.length = 0; };
</script>"""


def websocket_frame(payload):
    """One unmasked server-to-client binary frame (RFC 6455)."""
    length = len(payload)
    if length < 126:
        return bytes([0x82, length]) + payload
    if length < 65536:
        return bytes([0x82, 126]) + struct.pack(">H", length) + payload
    return bytes([0x82, 127]) + struct.pack(">Q", length) + payload


def websocket_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


shared = {"frame": None, "port": 0, "stop": threading.Event()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.startswith("/ws"):
            return self.upgrade()
        if self.path.startswith("/api/screen"):
            body = json.dumps({"screenshot": shared["frame"] or "", "url": "https://example.com"})
            return self.reply(body.encode(), "application/json")
        page = WS_VIEWER if "ws" in self.path else POLL_VIEWER
        return self.reply(page.replace("__PORT__", str(shared["port"])).encode(), "text/html")

    def reply(self, body, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def upgrade(self):
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", websocket_accept(self.headers.get("Sec-WebSocket-Key", "")))
        self.end_headers()
        self.wfile.flush()
        last = None
        try:
            while not shared["stop"].is_set():
                frame = shared["frame"]
                if frame and frame != last:
                    last = frame
                    # Binary on the wire: no base64 inflation, which is the one
                    # thing a push channel can offer that polling cannot.
                    self.connection.sendall(websocket_frame(base64.b64decode(frame)))
                time.sleep(0.002)
        except Exception:
            pass
        self.close_connection = True

    def log_message(self, *args):
        pass


def relay(source):
    """Publish the newest streamed frame, exactly as the app's endpoint would."""
    while not shared["stop"].is_set():
        frame = source.frame()
        if frame:
            shared["frame"] = frame
        time.sleep(0.002)


def measure(viewer, path, seconds=SECONDS):
    viewer.call("Page.navigate", url=f"http://127.0.0.1:{shared['port']}{path}")
    for _ in range(80):
        try:
            if viewer.evaluate("typeof window.__stats") == "function":
                break
        except Exception:
            pass
        time.sleep(0.05)
    time.sleep(0.8)
    before = json.loads(viewer.evaluate("JSON.stringify(window.__stats())"))
    start = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - start
    after = json.loads(viewer.evaluate("JSON.stringify(window.__stats())"))
    viewer.evaluate("window.__release()")
    return (
        (after["delivered"] - before["delivered"]) / elapsed,
        (after["painted"] - before["painted"]) / elapsed,
    )


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    shared["port"] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    source = Browser("data:text/html," + quote(SOURCE))
    viewer = None
    try:
        source.start_live()
        for _ in range(100):
            if source.frame():
                break
            time.sleep(0.05)
        threading.Thread(target=relay, args=(source,), daemon=True).start()

        viewer = Browser(f"http://127.0.0.1:{shared['port']}/")
        for _ in range(60):
            if viewer.evaluate("typeof window.__stats") == "function":
                break
            time.sleep(0.05)

        results = [("websocket", measure(viewer, "/?ws"))]
        for ms in POLL_INTERVALS:
            results.append((f"poll {ms or 'asap'}{' ms' if ms else ''}", measure(viewer, f"/?ms={ms}")))

        print(f"viewer canvas {viewer.evaluate(chr(100) + 'ocument.querySelector(\"#s\").naturalWidth')}px wide")
        print(f"{'transport':>14} {'delivered/s':>12} {'painted/s':>10}")
        for name, (delivered, painted) in results:
            print(f"{name:>14} {delivered:>12.1f} {painted:>10.1f}")
    finally:
        shared["stop"].set()
        if viewer:
            viewer.close()
        source.close()
        server.shutdown()


if __name__ == "__main__":
    main()
