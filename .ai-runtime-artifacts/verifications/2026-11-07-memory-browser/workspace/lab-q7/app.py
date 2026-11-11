"""qinglu Q7 健康探针服务。"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

BIND_HOST = os.environ.get("Q7_BIND_HOST", "127.0.0.1,::1")
PORT = int(os.environ.get("Q7_PORT", "8137"))
BIND_HOSTS = [item.strip() for item in BIND_HOST.split(",") if item.strip()]


class ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ready-q7":
            payload = json.dumps({"status": "ok", "probe": "RAVEN-42"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def serve(host: str) -> HTTPServer:
    """在指定地址上起一个探针服务器（按地址族选择 IPv4/IPv6）。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class _Server(HTTPServer):
        address_family = family

    return _Server((host, PORT), ProbeHandler)


def main() -> None:
    servers = []
    for host in BIND_HOSTS:
        server = serve(host)
        servers.append(server)
        print(f"probe listening on {host}:{PORT}", flush=True)

    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in servers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
