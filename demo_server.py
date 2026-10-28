"""Public, synthetic-only EMR demo; no model administration or original-restore routes."""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid
import zipfile
from xml.sax.saxutils import escape

from ClinicalCDSGateway.gateway.common import Denied, canonical, strict_json, exact_schema
from ClinicalTextWeb.service import DemoService, Session, examples, MESSAGES
from ClinicalTextWeb.llm_service import SURROGATE_PURPOSE

ROOT = Path(__file__).resolve().parent
RECORD_INFO = {
    "surrogate": ("경과기록", "외과", "2023-03-13", "수술 후 경과 및 과거력"),
    "bilingual": ("외래 초진기록", "순환기내과", "2023-03-10", "한영 혼용 진찰 기록"),
    "surgery": ("수술기록", "외과", "2023-03-10", "충수절제술 후 감염 평가"),
    "neonatal": ("입원기록", "신생아중환자실", "2023-03-09", "신생아 진단과 임상 경과"),
    "medication": ("외래 경과기록", "호흡기내과", "2023-03-08", "폐렴 치료와 약물 용량"),
}
PURPOSES = [
    {"id": "clinical", "label": "수술 후 경과·감염 연구", "purpose": SURROGATE_PURPOSE},
    {"id": "exact-age", "label": "연령별 수술 예후 연구", "purpose": SURROGATE_PURPOSE.replace("환자의 정확한 나이와 이름", "환자의 정확한 나이 47세는 연령별 분석에 필요하므로 반드시 보존한다. 이름")},
    {"id": "record-default", "label": "선택한 기록에 맞는 연구", "purpose": ""},
]


def spreadsheet_bytes(text, purpose, title):
    """Small real XLSX with inline strings: no formula interpretation, no external assets."""
    rows = [("항목", "내용"), ("문서", title), ("연구 목적", purpose), ("가명처리 기록", text), ("범위", "합성 EMR 시연 · 기관의 실제 반출 승인과 구분")]
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="1" max="1" width="20" customWidth="1"/><col min="2" max="2" width="100" customWidth="1"/></cols><sheetData>'
    for n, row in enumerate(rows, 1):
        sheet += f'<row r="{n}">' + ''.join(f'<c r="{col}{n}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>' for col, value in zip("AB", row)) + '</row>'
    sheet += '</sheetData></worksheet>'
    entries = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="연구용 기록" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


class VisitorService(DemoService):
    def new_session(self):
        with self.lock:
            now = time.time()
            expired = [key for key, value in self.sessions.items() if now - value.created >= 7200]
            for key in expired:
                self.sessions.pop(key, None)
                self.llm.configs.pop(key, None)
            if len(self.sessions) >= 128:
                raise Denied("WEB_LIMIT")
            identifier = secrets.token_urlsafe(32)
            self.sessions[identifier] = Session()
            self.llm.configure(identifier, self.local_model_config)
            return identifier, self.sessions[identifier]


class PublicDemo:
    def __init__(self, root, model_url="http://127.0.0.1:11435", model="qwen3:30b", service=None):
        self.service = service or VisitorService(root, local_model_config={"provider": "ollama", "base_url": model_url, "model": model, "verifier_model": model, "api_key": ""})
        self.model = model
        self.records = {item["id"]: item for item in examples()}
        self.jobs = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synthetic-llm")
        self.sequence = 0
        self.rate = defaultdict(deque)
        self.global_rate = deque()

    def bootstrap(self):
        identifier, session = self.service.new_session()
        return {"token": identifier, "model": self.model, "records": [{**item, "category": RECORD_INFO[item["id"]][0], "department": RECORD_INFO[item["id"]][1], "date": RECORD_INFO[item["id"]][2], "title": RECORD_INFO[item["id"]][3]} for item in self.records.values()], "purposes": PURPOSES, "scope": "synthetic-only", "expires_in": 7200}

    def submit(self, token, data, visitor):
        self.service.session(token)
        custom = type(data) is dict and "custom_note" in data
        exact_schema(data, {"record_id", "purpose_id", "purpose"} | ({"custom_note"} if custom else set()))
        if custom:
            from ClinicalCDSGateway.gateway.common import plain_text
            note = data["custom_note"]
            exact_schema(note, {"title", "text", "synthetic_confirmed"})
            if (note["synthetic_confirmed"] is not True or type(note["title"]) is not str or not 1<=len(note["title"].strip())<=60
                    or type(note["text"]) is not str or not 1<=len(note["text"].strip())<=4000 or len(note["text"].encode())>16000
                    or type(data["record_id"]) is not str or not data["record_id"].startswith("custom-")):
                raise Denied("WEB_INPUT_INVALID")
            from ClinicalTextWeb.input_text import normalize_text
            normalize_text(note["text"])
        if type(data["record_id"]) is not str or (not custom and data["record_id"] not in self.records) or data["purpose_id"] not in {p["id"] for p in PURPOSES}:
            raise Denied("WEB_INPUT_INVALID")
        if type(data["purpose"]) is not str or not 1 <= len(data["purpose"].strip()) <= 800:
            raise Denied("WEB_INPUT_INVALID")
        with self.lock:
            now = time.time()
            for key in [k for k,v in self.jobs.items() if v["state"] in {"done", "error"} and now-v["created"] > 7200]:
                self.jobs.pop(key, None)
            if any(job["owner"] == token and job["state"] in {"queued", "running"} for job in self.jobs.values()):
                raise Denied("DEMO_ALREADY_RUNNING")
            pending = sum(job["state"] in {"queued", "running"} for job in self.jobs.values())
            if pending >= 8:
                raise Denied("DEMO_QUEUE_FULL")
            for key in ("session:" + token, "visitor:" + visitor):
                while self.rate[key] and now-self.rate[key][0] >= 600:
                    self.rate[key].popleft()
                if len(self.rate[key]) >= (8 if key.startswith("session:") else 24):
                    raise Denied("DEMO_RATE_LIMIT")
            while self.global_rate and now-self.global_rate[0] >= 3600:
                self.global_rate.popleft()
            if len(self.global_rate) >= 180:
                raise Denied("DEMO_RATE_LIMIT")
            self.rate["session:"+token].append(now)
            self.rate["visitor:"+visitor].append(now)
            self.global_rate.append(now)
            self.sequence += 1
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"owner": token, "state": "queued", "created": now, "sequence": self.sequence, "record_id": data["record_id"]}
            self.executor.submit(self._run, job_id, copy.deepcopy(data))
            return {"job_id": job_id, "state": "queued", "position": pending}

    def _run(self, job_id, data):
        with self.lock:
            job = self.jobs[job_id]
            job["state"] = "running"
        record = ({"text":data["custom_note"]["text"], "checks":["treatment","measurements","time","negation","family"]}
                  if "custom_note" in data else self.records[data["record_id"]])
        try:
            view = self.service.analyze(job["owner"], {"engine": "llm", "purpose": data["purpose"].strip(), "text": record["text"], "checks": record["checks"], "required_phrases": [], "synthetic_consent": True})
            with self.lock:
                job.update(state="done", view=view, elapsed=round(time.time()-job["created"], 2))
        except Exception as error:
            code = error.code if isinstance(error, Denied) else "WEB_INTERNAL_ERROR"
            with self.lock:
                job.update(state="error", error=code, message=MESSAGES.get(code, "처리를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."))

    def job(self, token, job_id):
        self.service.session(token)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or not secrets.compare_digest(job["owner"], token):
                raise Denied("WEB_NOT_FOUND")
            result = {key: copy.deepcopy(value) for key, value in job.items() if key not in {"owner", "sequence"}}
            result["position"] = sum(j["sequence"] < job["sequence"] and j["state"] in {"queued", "running"} for j in self.jobs.values())
            return result

    def approve(self, token, data):
        return self.service.approve(token, data)

    def export(self, token, data):
        # Gateway rechecks approval and evidence, and emits its signed artifact first.
        result = self.service.export(token, data)
        view = result["view"]
        return {**result, "text_b64": base64.b64encode(view["output"].encode("utf-8-sig")).decode(), "xlsx_b64": base64.b64encode(spreadsheet_bytes(view["output"], view["purpose"], "가명처리 임상기록")).decode()}


ERRORS = {"DEMO_QUEUE_FULL": "현재 체험 요청이 많습니다. 앞선 처리가 끝난 뒤 다시 시도해 주세요.", "DEMO_ALREADY_RUNNING": "이미 진행 중인 처리가 있습니다.", "DEMO_RATE_LIMIT": "체험 횟수에 도달했습니다. 잠시 후 다시 시도해 주세요."}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, app, origin, port):
        self.app, self.origin = app, origin.rstrip("/")
        self.bootstrap_rate = defaultdict(deque)
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "ClinicalTextEMR"
    sys_version = ""
    def log_message(self, *_args):
        pass

    def visitor(self):
        import hashlib
        return hashlib.sha256(self.headers.get("CF-Connecting-IP", self.client_address[0]).encode()).hexdigest()

    def origin(self):
        origin = self.headers.get("Origin")
        same_host = {"https://"+self.headers.get("Host", ""), "http://"+self.headers.get("Host", "")}
        if origin and origin != self.server.origin and origin not in same_host:
            raise Denied("WEB_ORIGIN_DENIED")

    def token(self):
        value = self.headers.get("Authorization", "")
        if not value.startswith("Bearer ") or len(value) > 120:
            raise Denied("WEB_SESSION_EXPIRED")
        return value[7:]

    def send(self, code, value, content_type="application/json; charset=utf-8"):
        raw = value if type(value) is bytes else canonical(value)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.headers.get("Origin") == self.server.origin:
            self.send_header("Access-Control-Allow-Origin", self.server.origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(raw)

    def error(self, error):
        code = error.code if isinstance(error, Denied) else "WEB_INTERNAL_ERROR"
        status = 429 if code in {"WEB_LIMIT", "DEMO_RATE_LIMIT", "DEMO_QUEUE_FULL"} else 403 if code in {"WEB_ORIGIN_DENIED", "WEB_SESSION_EXPIRED", "WEB_NOT_FOUND"} else 400
        self.send(status, {"error": code, "message": ERRORS.get(code, MESSAGES.get(code, "요청을 처리하지 못했습니다."))})

    def do_OPTIONS(self):
        try:
            self.origin()
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.server.origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()
        except Exception as error:
            self.error(error)

    def do_GET(self):
        try:
            self.origin()
            path = urlsplit(self.path).path
            if path == "/api/health":
                self.send(200, {"status": "ok", "scope": "authored-synthetic-only", "model": self.server.app.model})
            elif path in {"/", "/styles.css", "/app.js", "/config.js", "/favicon.svg", "/qr.svg", "/qr.png"}:
                filename = "index.html" if path == "/" else path[1:]
                kinds = {".html":"text/html; charset=utf-8", ".css":"text/css; charset=utf-8", ".js":"text/javascript; charset=utf-8", ".svg":"image/svg+xml", ".png":"image/png"}
                asset = ROOT / "dist" / filename
                self.send(200, asset.read_bytes(), kinds[asset.suffix])
            elif path == "/api/bootstrap":
                key, now = self.visitor(), time.time()
                with self.server.app.lock:
                    bucket = self.server.bootstrap_rate[key]
                    while bucket and now-bucket[0] > 600:
                        bucket.popleft()
                    if len(bucket) >= 40:
                        raise Denied("DEMO_RATE_LIMIT")
                    bucket.append(now)
                self.send(200, self.server.app.bootstrap())
            elif path.startswith("/api/jobs/") and len(path.split("/")) == 4:
                self.send(200, self.server.app.job(self.token(), path.rsplit("/", 1)[-1]))
            else:
                self.send(404, {"error": "NOT_FOUND"})
        except Exception as error:
            self.error(error)

    def do_POST(self):
        try:
            self.connection.settimeout(10)
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("Transfer-Encoding") or not 0 < size <= 24000:
                raise Denied("SIZE_LIMIT")
            raw = self.rfile.read(size)
            self.origin()
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json" or len(raw) != size:
                raise Denied("WEB_INPUT_INVALID")
            data, token = strict_json(raw), self.token()
            path = urlsplit(self.path).path
            actions = {"/api/approve": self.server.app.approve, "/api/export": self.server.app.export}
            if path == "/api/analyze":
                self.send(202, self.server.app.submit(token, data, self.visitor()))
            elif path in actions:
                self.send(200, actions[path](token, data))
            else:
                self.send(404, {"error": "NOT_FOUND"})
        except Exception as error:
            self.error(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--model-url", default="http://127.0.0.1:11435")
    parser.add_argument("--model", default="qwen3:30b")
    args = parser.parse_args()
    if not args.origin.startswith(("https://", "http://127.0.0.1:")):
        parser.error("Use an HTTPS frontend origin (or loopback for development).")
    with tempfile.TemporaryDirectory(prefix="clinicaltext-public-") as root:
        app = PublicDemo(root, args.model_url, args.model)
        server = Server(app, args.origin, args.port)
        print(f"Public synthetic API listening on loopback port {args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            app.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
