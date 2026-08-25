"""測定用リフレクター(測定先)サーバー。

用途:
  1. 自社サーバーを測定先にする  — 本社・データセンター側でこれを起動し、
     config.yaml の測定先URLをこのサーバーに向けると、SpeedFusionトンネル越しの
     「実際に映像が通る経路」のボンディング性能を測定できる。
  2. --demo モードの裏側        — localhost で起動して測定エンジンの動作確認に使う。

エンドポイント(Cloudflare速度テストと同じ形):
  GET  /__down?bytes=N   … Nバイトのダミーデータを返す
  POST /__up             … 受信したボディを読み捨てる
  GET  /                 … 稼働確認

起動:  python3 run.py --serve --port 8123
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

_BLOB = bytes(256 * 1024)


class ReflectorHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BondingReflector/1.0"

    def log_message(self, *_args) -> None:  # 高頻度アクセスのためアクセスログは出さない
        pass

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == "/__down":
            try:
                size = int(parse_qs(parts.query).get("bytes", ["1048576"])[0])
            except ValueError:
                size = 1048576
            size = max(1, min(size, 1 << 31))
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            remaining = size
            try:
                while remaining > 0:
                    n = min(len(_BLOB), remaining)
                    self.wfile.write(_BLOB[:n])
                    remaining -= n
            except (BrokenPipeError, ConnectionResetError):
                pass  # 測定側の打ち切りは正常
            return
        if parts.path == "/":
            body = b"bonding-speedtest reflector OK\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/__up":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 測定側が締め切りで切断した場合(正常)


class _QuietServer(ThreadingHTTPServer):
    """測定側の途中切断(正常動作)でトレースバックを出さないサーバー。"""

    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: D102
        import sys
        exc = sys.exception()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve(host: str, port: int) -> None:
    httpd = _QuietServer((host, port), ReflectorHandler)
    print(f"リフレクターを起動しました: http://{host}:{port}/  (停止は Ctrl+C)")
    print("測定側 config.yaml の設定例:")
    print(f"  download_url: http://<このサーバーのIP>:{port}/__down?bytes={{bytes}}")
    print(f"  upload_url:   http://<このサーバーのIP>:{port}/__up")
    print(f"  rtt_host:     <このサーバーのIP>")
    print(f"  rtt_port:     {port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")
    finally:
        httpd.server_close()


def start_background(port: int) -> ThreadingHTTPServer:
    """--demo 用: バックグラウンドスレッドで起動してインスタンスを返す。"""
    import threading

    httpd = _QuietServer(("127.0.0.1", port), ReflectorHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
