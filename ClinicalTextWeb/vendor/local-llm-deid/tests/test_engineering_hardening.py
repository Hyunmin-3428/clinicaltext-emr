"""Regression tests for scoring integrity, reruns, and response handling.

All tests use only the bundled synthetic fixture or short literal strings. They
never contact a model server, require model weights, or read clinical data.
"""

from __future__ import annotations

import copy
import json
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from deid import metrics, pipeline  # noqa: E402
from deid.inference import LlamaServerClient  # noqa: E402
from deid.run_model import (  # noqa: E402
    _run_segments, _telemetry_envelope, build_parser, process_document, run_model,
)

FIXTURE = REPO_ROOT / "fixtures" / "synthetic"
NOTES_DIR = FIXTURE / "notes"
GOLD_DIR = FIXTURE / "gold"
PROTOCOL_DIR = REPO_ROOT / "protocol"
NAMES = [line.strip() for line in (FIXTURE / "name_gazetteer.txt").read_text().splitlines()
         if line.strip()]


def run_stub(notes_dir: Path, out_dir: Path, **kwargs) -> Path:
    return run_model(
        "offline-stub", notes_dir, out_dir, PROTOCOL_DIR,
        use_stub=True, name_gazetteer=NAMES, **kwargs,
    )


def all_row(results, pass_label: str = "cumulative_pass2", cohort: str = "all_expected"):
    return next(
        row for row in results["metrics"]
        if row["pass"] == pass_label and row["cohort"] == cohort and row["phi_type"] == "ALL"
    )


class ScoringScopeTest(unittest.TestCase):
    def test_gold_set_drives_expected_documents_and_missing_output_is_a_full_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one_note = root / "notes"
            one_note.mkdir()
            shutil.copyfile(NOTES_DIR / "TEST001.txt", one_note / "TEST001.txt")
            run_stub(one_note, root / "predictions")

            row = all_row(metrics.score_all(root / "predictions", GOLD_DIR))
            self.assertEqual(row["n_notes"], 3)
            self.assertEqual(row["gt_phi_chars"], 335)
            self.assertEqual(row["char_tp"], 132)
            self.assertAlmostEqual(row["char_recall"], 132 / 335)

    def test_prediction_document_without_matching_gold_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = run_stub(NOTES_DIR, root / "predictions")
            extra = model_dir / "resolved" / "EXTRA" / "resolved_predictions.json"
            extra.parent.mkdir(parents=True)
            extra.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "without matching gold.*EXTRA"):
                metrics.score_all(root / "predictions", GOLD_DIR)


class OutputLifecycleTest(unittest.TestCase):
    def test_existing_model_output_fails_closed_and_explicit_overwrite_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp) / "predictions"
            model_dir = run_stub(
                NOTES_DIR, out_dir, segment_size=100, overlap=10,
            )
            self.assertGreater(len(list(model_dir.rglob("*.raw.json"))), 6)

            with self.assertRaisesRegex(FileExistsError, "model output already exists"):
                run_stub(NOTES_DIR, out_dir)

            model_dir = run_stub(
                NOTES_DIR, out_dir, overwrite_model_output=True,
            )
            self.assertEqual(len(list(model_dir.rglob("*.raw.json"))), 6)
            self.assertEqual(
                sorted(path.name for path in (model_dir / "resolved").iterdir()),
                ["TEST001", "TEST002", "TEST003"],
            )

    def test_raw_content_is_stripped_by_default_and_retention_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            private_model = run_stub(NOTES_DIR, root / "private")
            private_raw = next(private_model.rglob("*.raw.json"))
            private_envelope = json.loads(private_raw.read_text(encoding="utf-8"))
            self.assertEqual(private_envelope["choices"][0]["message"]["content"], "")

            with self.assertRaisesRegex(ValueError, "acknowledge-phi-risk"):
                run_stub(NOTES_DIR, root / "unacknowledged", strip_content=False)

            retained_model = run_stub(
                NOTES_DIR, root / "retained", strip_content=False,
                acknowledge_phi_risk=True,
            )
            retained_raw = next(retained_model.rglob("*.raw.json"))
            retained_envelope = json.loads(retained_raw.read_text(encoding="utf-8"))
            self.assertNotEqual(retained_envelope["choices"][0]["message"]["content"], "")
            self.assertTrue(retained_envelope["content_retained"])

            parser_args = build_parser().parse_args([
                "--model-id", "test", "--notes-dir", str(NOTES_DIR),
                "--out-dir", str(root / "parser"),
            ])
            self.assertTrue(parser_args.strip_raw_content)

    def test_default_raw_projection_drops_unexpected_echo_fields(self) -> None:
        projected = _telemetry_envelope({
            "object": "chat.completion",
            "model": "test-model",
            "secret_echo": "Jane Patient, MRN 12345",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Jane Patient",
                    "reasoning_content": "MRN 12345",
                },
                "prompt_echo": "Jane Patient was admitted",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                      "unexpected": "Jane Patient"},
        })
        rendered = json.dumps(projected)
        self.assertNotIn("Jane Patient", rendered)
        self.assertNotIn("12345", rendered)
        self.assertEqual(projected["choices"][0]["message"]["content"], "")
        self.assertEqual(projected["usage"]["total_tokens"], 12)

    def test_symlinked_output_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.mkdir()
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked output directory"):
                run_stub(NOTES_DIR, linked)

    def test_prediction_and_metric_outputs_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = run_stub(NOTES_DIR, root / "predictions")
            raw_file = next(model_dir.rglob("*.raw.json"))
            self.assertEqual(stat.S_IMODE(model_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(raw_file.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(raw_file.stat().st_mode), 0o600)

            result_dir = root / "results"
            metrics.write_outputs(result_dir, GOLD_DIR,
                                  metrics.score_all(root / "predictions", GOLD_DIR))
            self.assertEqual(stat.S_IMODE(result_dir.stat().st_mode), 0o700)
            for path in result_dir.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_non_loopback_endpoint_requires_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "refusing non-loopback"):
                run_model(
                    "test", NOTES_DIR, Path(temp) / "predictions", PROTOCOL_DIR,
                    api_base="https://model.example.org",
                )

    def test_non_loopback_endpoint_requires_https_and_no_embedded_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "without HTTPS"):
                run_model(
                    "test", NOTES_DIR, Path(temp) / "http", PROTOCOL_DIR,
                    api_base="http://model.example.org", allow_remote_api=True,
                )
            with self.assertRaisesRegex(ValueError, "embedded credentials"):
                run_model(
                    "test", NOTES_DIR, Path(temp) / "credentials", PROTOCOL_DIR,
                    api_base="https://user:secret@model.example.org", allow_remote_api=True,
                )


class ConditionalValidationDenominatorTest(unittest.TestCase):
    def test_rate_uses_validation_records_not_raw_segment_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = run_stub(
                NOTES_DIR, root / "predictions", segment_size=100, overlap=10,
            )
            missing = (model_dir / "pass_2" / "validation" / "TEST001" /
                       "P2-S0002.validation.json")
            missing.unlink()

            row = all_row(metrics.score_all(root / "predictions", GOLD_DIR))
            self.assertEqual(row["expected_requests"], 10)
            self.assertEqual(row["usable_requests"], 9)
            self.assertEqual(row["usable_rate"], 1.0)
            self.assertEqual(row["json_valid_rate"], 1.0)


class PredictionArtifactIntegrityTest(unittest.TestCase):
    def test_schema_identity_hash_type_and_bounds_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = run_stub(NOTES_DIR, root / "predictions")
            target = model_dir / "resolved" / "TEST001" / "resolved_predictions.json"
            original = json.loads(target.read_text(encoding="utf-8"))

            def invalid_type(doc):
                doc["predictions"][0]["identifier_type"] = "NOT_A_CANONICAL_TYPE"

            def invalid_bounds(doc):
                doc["predictions"][0]["start"] = -1

            cases = [
                ("schema", lambda doc: doc.__setitem__("schema_version", "wrong")),
                ("document id", lambda doc: doc.__setitem__("document_id", "WRONG")),
                ("source hash", lambda doc: doc.__setitem__("source_sha256", "0" * 64)),
                ("identifier type", invalid_type),
                ("out of bounds", invalid_bounds),
            ]
            for label, mutate in cases:
                with self.subTest(label=label):
                    changed = copy.deepcopy(original)
                    mutate(changed)
                    target.write_text(json.dumps(changed, indent=2) + "\n", encoding="utf-8")
                    with self.assertRaises(metrics.PredictionArtifactError):
                        metrics.score_all(root / "predictions", GOLD_DIR)
                    target.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")


class FormatAndFinishReasonTest(unittest.TestCase):
    @staticmethod
    def envelope(content: str, finish_reason: str) -> dict:
        return {
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": "test-model",
            "object": "chat.completion",
        }

    def test_usable_format_is_measured_independently_of_finish_reason(self) -> None:
        content = json.dumps({
            "identifiers": [{"exact_text": "Alice", "identifier_type": "01_NAME"}]
        })

        class Client:
            def complete(self, _text):
                return FormatAndFinishReasonTest.envelope(content, "length")

        pairs, records = _run_segments(Client(), pipeline.fixed_segments("Alice"))
        self.assertEqual(pairs, [pipeline.Pair("Alice", "01_NAME")])
        self.assertTrue(records[0]["envelope_usable"])
        self.assertTrue(records[0]["strict_schema_valid"])
        self.assertEqual(records[0]["finish_reason"], "length")

    def test_nonstop_pass2_response_is_usable_but_not_applied(self) -> None:
        pass1 = self.envelope('{"identifiers":[]}', "stop")
        pass2 = self.envelope(
            '{"identifiers":[{"exact_text":"Alice","identifier_type":"01_NAME"}]}',
            "length",
        )

        class SequenceClient:
            def __init__(self):
                self.responses = iter([pass1, pass2])

            def complete(self, _text):
                return next(self.responses)

        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "test-model"
            process_document(SequenceClient(), "TEST", "Alice", model_dir)
            validation = json.loads(
                (model_dir / "pass_2" / "validation" / "TEST" /
                 "P2-S0001.validation.json").read_text(encoding="utf-8")
            )
            cumulative = json.loads(
                (model_dir / "pass_2" / "resolved" / "TEST" /
                 "resolved_predictions.json").read_text(encoding="utf-8")
            )
            self.assertTrue(validation["envelope_usable"])
            self.assertEqual(validation["finish_reason"], "length")
            self.assertEqual(cumulative["predictions"], [])


class MalformedServerResponseTest(unittest.TestCase):
    class Response:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.payload

    class Opener:
        def __init__(self, payload: bytes):
            self.payload = payload

        def open(self, *_args, **_kwargs):
            return MalformedServerResponseTest.Response(self.payload)

    def client_with_payload(self, payload: bytes) -> LlamaServerClient:
        client = LlamaServerClient("test-model", "", "{{CLINICAL_TEXT}}", {})
        client._opener = self.Opener(payload)
        return client

    def test_invalid_json_becomes_recorded_error_envelope(self) -> None:
        envelope = self.client_with_payload(b"not-json").complete("synthetic")
        self.assertEqual(envelope["choices"][0]["finish_reason"], "error")
        self.assertEqual(envelope["response_error"], "invalid_json")
        self.assertIn("latency_seconds", envelope)

    def test_nonobject_json_becomes_recorded_error_envelope(self) -> None:
        envelope = self.client_with_payload(b"[]").complete("synthetic")
        self.assertEqual(envelope["choices"][0]["finish_reason"], "error")
        self.assertEqual(envelope["response_error"], "non_object_json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
