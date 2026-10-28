import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
from xml.etree import ElementTree

from ClinicalCDSGateway.gateway.common import Denied
from demo_server import PublicDemo, Server, spreadsheet_bytes


class FakeService:
    def __init__(self):
        self.sessions = set()
        self.counter = 0
        self.release = {}
        self.gate = None
    def new_session(self):
        self.counter += 1
        token = str(self.counter)
        self.sessions.add(token)
        return token, None
    def session(self, token):
        if token not in self.sessions:
            raise Denied("WEB_SESSION_EXPIRED")
    def analyze(self, token, data):
        if self.gate:
            self.gate.wait(3)
        self.session(token)
        view = {"analysis_id": token, "review_digest": "bound", "state": "REVIEW_READY", "source": data["text"], "output": "합성 가명처리 결과", "purpose": data["purpose"]}
        self.release[token] = view
        return view
    def approve(self, token, data):
        self.session(token)
        if data.get("analysis_id") != token or data.get("review_digest") != "bound" or data.get("confirmed") is not True:
            raise Denied("WEB_REVIEW_CHANGED")
        self.release[token]["state"] = "APPROVED"
        return self.release[token]
    def export(self, token, data):
        self.session(token)
        if data.get("analysis_id") != token or self.release.get(token, {}).get("state") != "APPROVED":
            raise Denied("NOT_APPROVED_OR_CONSUMED")
        self.release[token]["state"] = "EXPORTED"
        return {"view":self.release[token], "artifact_b64":base64.b64encode(b"signed-by-gateway-test-double").decode()}


class PublicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeService()
        self.app = PublicDemo(self.tmp.name, service=self.fake)
        self.token = self.app.bootstrap()["token"]
        self.data = {"record_id":"surrogate", "purpose_id":"clinical", "purpose":"수술 날짜와 3일 경과, 가족력 보존"}
    def tearDown(self):
        if self.fake.gate:
            self.fake.gate.set()
        self.app.executor.shutdown(wait=True)
        self.tmp.cleanup()
    def wait(self, job):
        for _ in range(100):
            result = self.app.job(self.token, job["job_id"])
            if result["state"] in {"done", "error"}:
                return result
            time.sleep(.01)
        self.fail("Worker did not finish")
    def test_sources_are_authored_and_calendar_dates_present(self):
        boot = self.app.bootstrap()
        self.assertEqual(len(boot["records"]), 5)
        self.assertIn("2023년 3월 10일", boot["records"][0]["text"])
        self.assertNotIn("base_url", boot)
    def test_arbitrary_source_field_rejected(self):
        with self.assertRaises(Denied):
            self.app.submit(self.token, {**self.data, "text":"user-provided source"}, "ip")
    def test_unknown_record_rejected(self):
        with self.assertRaises(Denied):
            self.app.submit(self.token, {**self.data, "record_id":"../../private"}, "ip")
    def test_custom_synthetic_note_can_be_analyzed(self):
        data={**self.data,"record_id":"custom-123","custom_note":{"title":"새 수술기록","text":"합성 환자. 충수절제술. 수술 3일 후 발열 없음.","synthetic_confirmed":True}}
        self.assertEqual(self.wait(self.app.submit(self.token,data,"ip"))["state"],"done")
    def test_custom_note_requires_synthetic_confirmation_and_bounded_text(self):
        note={"title":"새 기록","text":"가상 기록","synthetic_confirmed":False}
        for change in ({},{"synthetic_confirmed":True,"text":"x"*4001},{"synthetic_confirmed":True,"title":""}):
            with self.assertRaises(Denied):
                self.app.submit(self.token,{**self.data,"record_id":"custom-123","custom_note":{**note,**change}},"ip")
    def test_bounded_purpose(self):
        for value in ("", "a"*801, [], None):
            with self.assertRaises(Denied):
                self.app.submit(self.token, {**self.data, "purpose":value}, "ip")
    def test_job_result_is_session_scoped(self):
        job = self.app.submit(self.token, self.data, "ip")
        result = self.wait(job)
        self.assertEqual(result["state"], "done")
        other = self.app.bootstrap()["token"]
        with self.assertRaises(Denied):
            self.app.job(other, job["job_id"])
    def test_one_active_job_per_visitor(self):
        self.fake.gate = threading.Event()
        self.app.submit(self.token, self.data, "ip")
        with self.assertRaisesRegex(Denied, "DEMO_ALREADY_RUNNING"):
            self.app.submit(self.token, self.data, "ip")
    def test_queue_is_bounded(self):
        self.fake.gate = threading.Event()
        for index in range(8):
            token = self.app.bootstrap()["token"]
            self.app.submit(token, self.data, str(index))
        with self.assertRaisesRegex(Denied, "DEMO_QUEUE_FULL"):
            self.app.submit(self.token, self.data, "ip")
    def test_approval_then_multiple_format_export(self):
        self.wait(self.app.submit(self.token, self.data, "ip"))
        data = {"analysis_id":self.token, "review_digest":"bound", "confirmed":True}
        with self.assertRaises(Denied):
            self.app.export(self.token, data)
        self.app.approve(self.token, data)
        exported = self.app.export(self.token, data)
        self.assertEqual(base64.b64decode(exported["text_b64"]).decode("utf-8-sig"), "합성 가명처리 결과")
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["xlsx_b64"]))) as z:
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
        with self.assertRaises(Denied):
            self.app.export(self.token, data)
    def test_real_xlsx_escapes_xml_and_cannot_create_formula(self):
        raw = spreadsheet_bytes("=HYPERLINK(\"x\") < & >", "목적", "문서")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist():
                ElementTree.fromstring(archive.read(name))
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
            self.assertNotIn("<f>", sheet)
            self.assertIn("&lt;", sheet)
            self.assertIn('t="inlineStr"', sheet)


class HTTPTests(PublicTests):
    def setUp(self):
        super().setUp()
        self.server = Server(self.app, "https://demo.example", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()
    def get(self, path, origin=None, token=None):
        headers = {}
        if origin: headers["Origin"] = origin
        if token: headers["Authorization"] = "Bearer " + token
        return urlopen(Request(self.url+path, headers=headers), timeout=3)
    def test_cross_origin_denied(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/api/bootstrap", "https://attacker.example")
        self.assertEqual(error.exception.code, 403)
    def test_preflight_allows_only_frontend(self):
        request = Request(self.url+"/api/analyze", method="OPTIONS", headers={"Origin":"https://demo.example"})
        with urlopen(request) as response:
            self.assertEqual(response.status,204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"],"https://demo.example")
    def test_admin_ollama_restore_and_files_not_exposed(self):
        for path in ("/api/model/configure", "/api/shutdown", "/api/restore", "/api/tags", "/.runtime/local-model.json", "/../../README.md"):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)
    def test_private_job_requires_session_token(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/api/jobs/abc")
        self.assertEqual(error.exception.code,403)
    def test_bootstrap_has_cors_and_no_cache(self):
        with self.get("/api/bootstrap", "https://demo.example") as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"],"https://demo.example")
            self.assertEqual(response.headers["Cache-Control"],"no-store")
            self.assertEqual(json.load(response)["scope"], "synthetic-only")


if __name__ == "__main__":
    unittest.main()
