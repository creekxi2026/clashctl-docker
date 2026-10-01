"""Real isolated subscription, origin and HTTP upstream proxy servers."""
import base64
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"generation": 1}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, code=200):
        body = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        port = self.server.server_port
        if port == 18081:
            # Only this real upstream proxy knows how to reach target.invalid.
            if self.path.startswith("http://target.invalid/"):
                req = urllib.request.Request("http://127.0.0.1:18080/origin", headers={"X-Test-Hop": "yes"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    return self.send(r.read())
            return self.send("unsupported proxy target", 502)
        if port == 18080:
            if self.path == "/204":
                return self.send(b"", 204)
            return self.send("proxy-hop-ok" if self.headers.get("X-Test-Hop") == "yes" else "direct-origin")
        if self.path == "/bump":
            STATE["generation"] += 1
            return self.send("updated")
        if self.path == "/bad":
            return self.send("<html>invalid subscription</html>")
        if self.path == "/convert":
            creds = base64.urlsafe_b64encode(b"aes-128-gcm:fixture-only-not-a-real-credential").decode().rstrip("=")
            return self.send(base64.b64encode(f"ss://{creds}@fixture:12345#fixture-ss".encode()))
        if self.path != "/sub":
            return self.send("not found", 404)
        generation = STATE["generation"]
        return self.send(f"""mixed-port: 7890
mode: rule
log-level: info
test-generation: {generation}
proxies:
  - {{name: hop-a, type: http, server: fixture, port: 18081}}
  - {{name: hop-b, type: http, server: fixture, port: 18081}}
proxy-groups:
  - name: PROXY
    type: select
    proxies: [hop-a, hop-b]
rules:
  - MATCH,PROXY
""")

if __name__ == "__main__":
    servers = [ThreadingHTTPServer(("0.0.0.0", port), Handler) for port in (18080, 18081, 18082)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()
