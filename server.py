"""Loopback-only development host. Replace this host before any public deployment."""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
from app.domain import investigate, markdown_report
from app.providers import PROVIDERS, SampleProvider, fetch_snapshot, ProviderUnavailable
from app.storage import CaseStore

ROOT = Path(__file__).resolve().parent

def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, value, status=200, kind="application/json; charset=utf-8", attachment=None):
            body = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False).encode() if kind.startswith("application/json") else value.encode())
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if attachment:
                self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
            self.end_headers()
            self.wfile.write(body)

        def guard(self, write=False):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:
                raise PermissionError("Unrecognized local host.")
            if write:
                origin = self.headers.get("Origin")
                if origin and origin not in {"http://" + a for a in allowed}:
                    raise PermissionError("Cross-origin writes are not allowed.")
                if self.headers.get("X-Local-Investigation") != "1":
                    raise PermissionError("A local application request is required.")

        def do_GET(self):
            try:
                self.guard()
                path = urlparse(self.path).path
                if path == "/api/providers": return self.send(PROVIDERS)
                if path == "/api/health": return self.send({"status": "ok", "mode": "local", "version": "0.1.0"})
                if path == "/api/cases": return self.send(store.list())
                if path == "/api/demo-bundle":
                    # Fixed authored fixture only; never expose a case archive by path.
                    return self.send((ROOT / "fixtures" / "trace-demo.json").read_bytes())
                if path == "/portfolio.css":
                    return self.send((ROOT / "dist" / "portfolio.css").read_bytes(), kind="text/css; charset=utf-8")
                if path.startswith("/api/samples/"):
                    key = path.rsplit("/", 1)[-1]
                    return self.send({"id": None, "title": "A withdrawal with an unresolved destination" if key == "routing" else "Ordinary funding and market activity", "notes": [], "analysis": investigate(SampleProvider().fetch(key))})
                match = re.fullmatch(r"/api/cases/(case_[a-f0-9]{16})(/report|/snapshot)?", path)
                if match:
                    case = store.get(match[1])
                    if match[2] == "/report": return self.send(markdown_report(case), kind="text/markdown; charset=utf-8", attachment=match[1]+".md")
                    if match[2] == "/snapshot":
                        snap = case["analysis"]["snapshot"]
                        if not snap["source"]["export_allowed"]: raise PermissionError("Source policy does not permit export.")
                        return self.send(snap, attachment=match[1]+".json")
                    return self.send(case)
                assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
                if path not in assets: return self.send({"error": "Not found."}, 404)
                name, kind = assets[path]
                return self.send((ROOT / "dist" / name).read_bytes(), kind=kind+"; charset=utf-8")
            except Exception as exc: self.failure(exc)

        def do_POST(self):
            try:
                self.guard(write=True)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000 or not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("A JSON body of at most 1 MB is required.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict): raise ValueError("Expected an object.")
                path = urlparse(self.path).path
                if path == '/api/cases/import-bundle':
                    return self.send(store.import_case(body.get('bundle')), 201)
                if path == "/api/cases":
                    snapshot = fetch_snapshot(body.get("provider"), body.get("subject", ""), body.get("snapshot"))
                    # Receipt metadata must enter through the validated bundle route.
                    for key in ('receipt_evidence', 'receipt_review', 'bundle_import', 'trace_evidence', 'trace_review'):
                        snapshot.pop(key, None)
                    return self.send(store.create(body.get("title", "Investigation"), snapshot), 201)
                match = re.fullmatch(r"/api/cases/(case_[a-f0-9]{16})/notes", path)
                if match: return self.send(store.note(match[1], body.get("text"), body.get("disposition")))
                self.send({"error": "Not found."}, 404)
            except Exception as exc: self.failure(exc)

        def failure(self, exc):
            if isinstance(exc, ProviderUnavailable): return self.send({"error": str(exc), "code": exc.code}, 502)
            if isinstance(exc, PermissionError): return self.send({"error": str(exc)}, 403)
            if isinstance(exc, KeyError): return self.send({"error": "Case not found."}, 404)
            if isinstance(exc, (ValueError, TypeError)): return self.send({"error": str(exc)}, 400)
            self.send({"error": "The local operation failed. No conclusion was produced."}, 500)
    return Handler

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=str(ROOT / ".runtime" / "cases.sqlite3"))
    args = parser.parse_args()
    host = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(CaseStore(args.db)))
    print(f"Local: http://127.0.0.1:{host.server_port}", flush=True)
    try: host.serve_forever()
    except KeyboardInterrupt: pass
    finally: host.server_close()
