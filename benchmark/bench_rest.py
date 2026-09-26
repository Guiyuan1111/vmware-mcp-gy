# -*- coding: utf-8 -*-
"""bench_rest —— REST 连接复用基准（0.3.1 轮5）

口径：本机起一个线程化 HTTP/1.1 keep-alive 测试服务器（记录每请求的 TCP 客户端端口），
      对比两种客户端形态各发 N 个请求：
  old = v0.2.x 行为：每请求新建 httpx.AsyncClient（新 TCP 连接）
  new = 0.3.x 行为：进程级共享连接池（VMwareClient，keep-alive 复用同一 TCP 连接）
证据：服务器观测到的独立客户端端口数 = TCP 连接数；同时测墙钟。
      （vmrest 需凭据，本基准用本地测试服务器给出连接行为的可复现证据。）

运行：python benchmark/bench_rest.py [N]
"""
import asyncio
import json
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
from vmware_mcp.client import VMwareClient  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "bench/1"

    def do_GET(self):
        peer = self.request.getpeername()
        self.server.seen_ports.add(peer[1])
        body = json.dumps([{"id": "vm-1", "path": "D:/vms/a.vmx"}]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


async def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen_ports = set()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"

    try:
        # old：每请求新建客户端（v0.2.x client.py 形态）
        async def old_request():
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{base}/api/vms")
                r.raise_for_status()
                return r.json()

        t0 = time.perf_counter()
        for _ in range(N):
            await old_request()
        old_ms = (time.perf_counter() - t0) * 1000
        old_conns = len(srv.seen_ports)

        # new：共享池（当前 VMwareClient 形态）
        srv.seen_ports.clear()
        wc = VMwareClient(host="127.0.0.1", port=port)
        wc.base_url = f"{base}/api"
        t0 = time.perf_counter()
        for _ in range(N):
            await wc.list_vms()
        new_ms = (time.perf_counter() - t0) * 1000
        new_conns = len(srv.seen_ports)

        print(f"# bench_rest  N={N} requests, 本地 HTTP/1.1 keep-alive 测试服务器")
        print(f"{'形态':<30}{'TCP连接数':>10}{'墙钟(ms)':>10}{'均值(ms/req)':>13}")
        print(f"{'old 每请求新建 AsyncClient(0.2.x)':<29}{old_conns:>10}{old_ms:>10.0f}{old_ms / N:>13.2f}")
        print(f"{'new 共享连接池(0.3.x)':<28}{new_conns:>10}{new_ms:>10.0f}{new_ms / N:>13.2f}")
        print(json.dumps({
            "bench": "rest", "n": N,
            "old_connections": old_conns, "new_connections": new_conns,
            "old_ms": round(old_ms), "new_ms": round(new_ms),
        }, ensure_ascii=False))
    finally:
        srv.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
