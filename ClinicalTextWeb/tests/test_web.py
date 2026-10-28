from concurrent.futures import ThreadPoolExecutor
import base64
import copy
import http.client
import json
import tempfile
import threading
import unittest

from ClinicalCDSGateway.gateway.common import Denied
from ClinicalCDSGateway.gateway.engine import PAYLOAD_KEYS, verify_external
from ClinicalCDSGateway.gateway.fixtures import FIXTURES
from ClinicalTextWeb.clinical import verify_relations
from ClinicalTextWeb.server import LocalServer
from ClinicalTextWeb.service import DemoService


def data(**changes):
    return {"purpose": "심혈관 연구를 위한 혈압과 질환 부정 표현 보존", "text": FIXTURES["connected-hybrid"]["source"],
            "checks": ["measurements", "negation"], "required_phrases": []} | changes


def review(view, **changes):
    return {"analysis_id": view["analysis_id"], "review_digest": view["review_digest"], "confirmed": True} | changes


class EvidenceTests(unittest.TestCase):
    def test_number_unit_negation_and_time_damage_are_detected(self):
        for source, output, kind in [
            ("BP 120/80 mmHg.", "BP 120/90 mmHg.", "measurements"),
            ("insulin 10 U SC.", "insulin 10 mg SC.", "measurements"),
            ("발열 없음.", "발열 있음.", "negation"),
            ("No evidence of MI.", "Evidence of MI.", "negation"),
            ("수술 3일 후 발열 없음.", "수술 5일 후 발열 없음.", "time")]:
            with self.subTest(kind=kind, source=source):
                result = verify_relations(source, output, [kind], "임상 연구", [])
                self.assertGreater(result["failed"], 0)
                self.assertFalse(result["eligible"])

    def test_purpose_adds_mandatory_surgical_conditions(self):
        text = "No evidence of MI. BP 120/80 mmHg."
        result = verify_relations(text, text, ["measurements"], "수술 후 감염 연구", [])
        self.assertIn("time", result["effective_checks"])
        self.assertGreater(result["unknown"], 0)
        self.assertFalse(result["eligible"])

    def test_missing_custom_source_is_unknown_not_passed(self):
        result = verify_relations("발열 없음.", "발열 없음.", ["negation"], "연구", ["수술 3일 후"])
        self.assertEqual(result["unknown"], 1)
        self.assertFalse(result["eligible"])

    def test_repeated_or_added_facts_are_not_silently_accepted(self):
        for source, output in [("BP 120/80 mmHg. BP 120/80 mmHg.", "BP 120/80 mmHg."),
                               ("BP 120/80 mmHg.", "BP 120/80 mmHg. BP 150/90 mmHg.")]:
            self.assertFalse(verify_relations(source, output, ["measurements"], "연구", [])["eligible"])

    def test_optional_absent_condition_is_explicit(self):
        result = verify_relations("BP 120/80 mmHg.", "BP 120/80 mmHg.", ["measurements", "family"], "연구", [])
        self.assertTrue(result["eligible"])
        self.assertTrue(any(row["status"] == "not_present" for row in result["rows"]))


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="clinical-web-test-")
        cls.service = DemoService(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.owner, _ = self.service.new_session()

    def assert_denied(self, code, action):
        with self.assertRaises(Denied) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_actual_hybrid_approval_and_one_shot_public_export(self):
        view = self.service.analyze(self.owner, data())
        self.assertTrue(view["can_approve"])
        self.assertEqual(view["phi_count"], 2)
        self.assertNotIn("김민준", view["output"])
        self.assertNotIn("John Smith", view["output"])
        self.assertGreaterEqual(view["validation"]["passed"], 2)
        self.assert_denied("NOT_APPROVED_OR_CONSUMED", lambda: self.service.export(self.owner, review(view)))
        self.assert_denied("WEB_CONFIRM_REQUIRED", lambda: self.service.approve(self.owner, review(view, confirmed=False)))
        self.assert_denied("WEB_REVIEW_CHANGED", lambda: self.service.approve(self.owner, review(view, review_digest="0" * 64)))
        stranger, _ = self.service.new_session()
        self.assert_denied("WEB_NOT_FOUND", lambda: self.service.approve(stranger, review(view)))
        approved = self.service.approve(self.owner, review(view))
        self.assertEqual(approved["state"], "APPROVED")

        def export_once(_):
            try:
                return self.service.export(self.owner, review(view))
            except Denied as error:
                return {"error": error.code}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(export_once, range(2)))
        exported = [result for result in results if result.get("state") == "EXPORTED"]
        self.assertEqual(len(exported), 1)
        raw = base64.b64decode(exported[0]["artifact_b64"])
        envelope = json.loads(raw)
        payload = verify_external(envelope, base64.b64decode(exported[0]["verification_key_b64"]))
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        self.assertEqual(payload["text"], view["output"])
        for forbidden in [b'"source"', b'"mapping"', b'"capsule"', b'"key"', "김민준".encode(), b"John Smith", data()["purpose"].encode()]:
            self.assertNotIn(forbidden, raw)

    def test_unregistered_note_is_shown_but_cannot_be_approved(self):
        view = self.service.analyze(self.owner, data(text="성명: 김민준. No evidence of MI. BP 120/80 mmHg."))
        self.assertIn("BP 120/80", view["output"])
        self.assertFalse(view["registered_example"])
        self.assertFalse(view["can_approve"])
        self.assert_denied("WEB_HELD", lambda: self.service.approve(self.owner, review(view)))

    def test_privacy_conflict_with_required_phrase_blocks_release(self):
        view = self.service.analyze(self.owner, data(required_phrases=["김민준"]))
        self.assertFalse(view["can_approve"])
        self.assertGreater(view["validation"]["failed"], 0)
        self.assert_denied("WEB_HELD", lambda: self.service.approve(self.owner, review(view)))

    def test_edit_invalidates_existing_approval(self):
        view = self.service.analyze(self.owner, data())
        self.service.approve(self.owner, review(view))
        self.service.invalidate(self.owner, {"analysis_id": view["analysis_id"]})
        self.assert_denied("WEB_HELD", lambda: self.service.export(self.owner, review(view)))

    def test_server_purpose_tamper_is_bound_to_review(self):
        view = self.service.analyze(self.owner, data())
        entry = self.service.sessions[self.owner].analyses[view["analysis_id"]]
        entry["input"]["purpose"] += " 변경"
        self.assert_denied("WEB_REVIEW_CHANGED", lambda: self.service.approve(self.owner, review(view)))
        entry["input"]["purpose"] = data()["purpose"]
        entry["input"]["text"] = entry["input"]["text"].replace("김민준", "김철수")
        self.assert_denied("WEB_REVIEW_CHANGED", lambda: self.service.approve(self.owner, review(view)))
        entry["input"]["text"] = data()["text"]
        entry["view"]["output"] += " "
        self.assert_denied("WEB_REVIEW_CHANGED", lambda: self.service.approve(self.owner, review(view)))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="clinical-web-http-test-")
        cls.service = DemoService(cls.temp.name)
        cls.server = LocalServer(cls.service, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def test_assets_bootstrap_and_private_paths(self):
        for path in ["/", "/app.js", "/app.css"]:
            status, headers, _ = self.call("GET", path)
            self.assertEqual(status, 200)
            self.assertIn("no-store", headers["Cache-Control"])
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, headers, body = self.call("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertNotIn("tokens", json.loads(body))
        for path in ["/internal/vault.key", "/../ClinicalCDSGateway/README.md"]:
            self.assertEqual(self.call("GET", path)[0], 404)

    def test_host_origin_and_missing_csrf_are_rejected(self):
        self.assertEqual(self.call("GET", "/api/bootstrap", headers={"Host": "example.org"})[0], 403)
        self.assertEqual(self.call("GET", "/api/bootstrap", headers={"Origin": "https://example.org"})[0], 403)
        _, headers, body = self.call("GET", "/api/bootstrap")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.call("POST", "/api/analyze", json.dumps(data()), {"Cookie": cookie, "Content-Type": "application/json"})[0], 403)

    def test_actual_browser_api_flow(self):
        _, headers, raw = self.call("GET", "/api/bootstrap")
        bootstrap = json.loads(raw)
        request_headers = {"Cookie": headers["Set-Cookie"].split(";", 1)[0], "Content-Type": "application/json", "X-CSRF-Token": bootstrap["csrf"]}
        status, _, body = self.call("POST", "/api/analyze", json.dumps(data()), request_headers)
        self.assertEqual(status, 200)
        view = json.loads(body)
        self.assertTrue(view["can_approve"])
        status, _, _ = self.call("POST", "/api/approve", json.dumps(review(view)), request_headers)
        self.assertEqual(status, 200)
        status, _, body = self.call("POST", "/api/export", json.dumps(review(view)), request_headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"], "EXPORTED")

    def test_shutdown_requires_session_and_csrf_then_stops_server(self):
        local = LocalServer(self.service, 0)
        worker = threading.Thread(target=local.serve_forever, daemon=True)
        worker.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", local.server_port, timeout=5)
            conn.request("POST", "/api/shutdown", body="{}", headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            conn.close()
            identifier, session = self.service.new_session()
            conn = http.client.HTTPConnection("127.0.0.1", local.server_port, timeout=5)
            conn.request("POST", "/api/shutdown", body="{}", headers={"Content-Type": "application/json", "Cookie": "clinical_demo_session=" + identifier, "X-CSRF-Token": session.csrf})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            conn.close()
            worker.join(3)
            self.assertFalse(worker.is_alive())
        finally:
            local.shutdown()
            local.server_close()


if __name__ == "__main__":
    unittest.main()
