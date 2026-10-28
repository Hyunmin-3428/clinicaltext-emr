"""Loopback-only HTTP entry point, using the already installed Python environment."""
from __future__ import annotations

import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
import webbrowser

from ClinicalCDSGateway.gateway.common import Denied, canonical, strict_json, exact_schema
from .service import DemoService, MESSAGES

STATIC = Path(__file__).with_name("static")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}
# Fixed authored-synthetic deliverables only; no directory or arbitrary path API.
for _filename in ("comparison.html", "comparison.css", "oss-comparison.json", "oss-comparison-initial.json", "stage-audit.json", "detection-audit.json", "connectivity-status.json"):
    ASSETS["/evaluation/" + _filename] = (
        "../docs/evaluation-2026-10-28/" + _filename,
        "text/html; charset=utf-8" if _filename.endswith(".html") else "text/css; charset=utf-8" if _filename.endswith(".css") else "application/json; charset=utf-8")
for _filename in ("report.html", "report.css", "stage-audit.json", "comparison.json", "functional-tests.log", "status.json", "job-source.json", "local-signature-checks.json", "prior-v2-stage-failed.json", "prior-v3-stage-failed.json", "privacy-role-checks.json"):
    _kind="text/html" if _filename.endswith(".html") else "text/css" if _filename.endswith(".css") else "application/json" if _filename.endswith(".json") else "text/plain"
    ASSETS["/evaluation/latest/"+_filename] = ("../docs/evaluation-2026-10-28-final/"+_filename,_kind+"; charset=utf-8")
for _case in ("surrogate", "surrogate_exact_age", "bilingual", "surgery", "neonatal"):
    for _suffix in ("public.json", "verification.key"):
        _filename=_case+"."+_suffix
        ASSETS["/evaluation/latest/"+_filename] = ("../docs/evaluation-2026-10-28-final/"+_filename,"application/json" if _suffix.endswith("json") else "application/octet-stream")


def local_model_config():
    """Persist only a loopback Ollama preference, never credentials or notes."""
    path = Path(__file__).with_name(".runtime") / "local-model.json"
    if not path.is_file():
        return None
    from .llm_client import ModelConfig
    config = ModelConfig.parse(strict_json(path.read_bytes()))
    if config.provider != "ollama" or config.remote or config.api_key:
        raise Denied("LLM_CONFIG_INVALID")
    return {"provider": config.provider, "base_url": config.base_url, "model": config.model,
            "verifier_model": config.verifier_model, "api_key": ""}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, port=8765):
        self.service = service
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClinicalTextDemo"
    sys_version = ""

    def log_message(self, *_args):
        pass  # Never log note contents, cookies, paths or query strings.

    def _guard_host(self):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            raise Denied("WEB_ORIGIN_DENIED")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in allowed}:
            raise Denied("WEB_ORIGIN_DENIED")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise Denied("WEB_ORIGIN_DENIED")

    def _session_id(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["clinical_demo_session"].value if "clinical_demo_session" in cookie else ""
        except Exception:
            return ""

    def _send(self, status, raw, content_type="application/json; charset=utf-8", cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", f"clinical_demo_session={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, error):
        code = error.code if isinstance(error, Denied) else "WEB_INTERNAL_ERROR"
        status = 403 if code in {"WEB_ORIGIN_DENIED", "WEB_CSRF_DENIED", "WEB_SESSION_EXPIRED", "WEB_NOT_FOUND"} else 409 if code in {"WEB_HELD", "WEB_REVIEW_CHANGED", "NOT_APPROVED_OR_CONSUMED"} else 400
        self._send(status, canonical({"error": code, "message": MESSAGES.get(code, "요청을 완료하지 못했습니다. 입력을 확인하거나 다시 검사해 주세요.")}))

    def do_GET(self):
        try:
            self._guard_host()
            path = urlsplit(self.path).path
            if path in ASSETS:
                filename, content_type = ASSETS[path]
                self._send(200, (STATIC / filename).read_bytes(), content_type)
            elif path == "/api/health":
                self._send(200, canonical({"status": "ok", "application": "clinical-text-web", "deployment": "local-synthetic-only"}))
            elif path == "/api/bootstrap":
                identifier, created = self._session_id(), False
                try:
                    self.server.service.session(identifier)
                except Denied:
                    identifier, _ = self.server.service.new_session()
                    created = True
                self._send(200, canonical(self.server.service.bootstrap(identifier)), cookie=identifier if created else None)
            else:
                self._send(404, canonical({"error": "NOT_FOUND"}))
        except Exception as error:
            self._error(error)

    def do_POST(self):
        try:
            if self.headers.get("Transfer-Encoding"):
                raise Denied("WEB_INPUT_INVALID")
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise Denied("WEB_INPUT_INVALID") from None
            if not 0 < size <= 70000:
                raise Denied("SIZE_LIMIT")
            # Consume a bounded body before sending an early rejection. Closing a
            # Windows socket with unread request bytes can reset a valid response.
            self.connection.settimeout(10)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise Denied("WEB_INPUT_INVALID")
            self._guard_host()
            identifier = self._session_id()
            session = self.server.service.session(identifier)
            import hmac
            csrf = self.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(csrf, session.csrf):
                raise Denied("WEB_CSRF_DENIED")
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise Denied("WEB_INPUT_INVALID")
            data = strict_json(raw)
            if urlsplit(self.path).path == "/api/shutdown":
                exact_schema(data, set())
                self._send(200, canonical({"state": "STOPPING"}))
                import threading
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            routes = {"/api/analyze": self.server.service.analyze, "/api/approve": self.server.service.approve,
                      "/api/export": self.server.service.export, "/api/invalidate": self.server.service.invalidate,
                      "/api/model/configure": self.server.service.llm.configure, "/api/model/probe": self.server.service.llm.probe}
            action = routes.get(urlsplit(self.path).path)
            if action is None:
                self._send(404, canonical({"error": "NOT_FOUND"}))
                return
            self._send(200, canonical(action(identifier, data)))
        except Exception as error:
            self._error(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ClinicalText local browser demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local demo in your browser")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    with tempfile.TemporaryDirectory(prefix="clinical-text-web-") as directory:
        service = DemoService(directory, local_model_config=local_model_config())
        try:
            server = LocalServer(service, args.port)
        except OSError:
            parser.exit(1, "Port unavailable. Choose another port with --port.\n")
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"ClinicalText demo: {url}", flush=True)
        print("Local synthetic demo. Press Ctrl+C to stop.", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
