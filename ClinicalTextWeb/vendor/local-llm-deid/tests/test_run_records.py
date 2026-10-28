"""Synthetic regression tests for run provenance and expected segment coverage."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from deid import metrics, pipeline, provenance
from deid.run_model import build_parser, process_document, run_model

spec = importlib.util.spec_from_file_location(
    "long_fixture", ROOT / "fixtures" / "long_synthetic" / "build_fixture.py")
long_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(long_fixture)


def envelope(pairs, reason="stop"):
    content = json.dumps({"identifiers": [
        {"exact_text": text, "identifier_type": label} for text, label in pairs]})
    return {"choices": [{"finish_reason": reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class RunRecordsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture"
        long_fixture.write_fixture(self.fixture)
        self.pred = self.root / "predictions"
        self.model = run_model(
            "stub", self.fixture / "notes", self.pred, ROOT / "protocol",
            use_stub=True, name_gazetteer=long_fixture.NAMES)

    def ops(self, pass_number=2):
        base = self.model / "pass_2" if pass_number == 2 else self.model
        return metrics.doc_operations(base, long_fixture.DOC_ID, pass_number)

    def remove_record(self, subtree, scope="P2-S0002"):
        suffix = ".raw.json" if subtree == "raw_responses" else ".validation.json"
        (self.model / "pass_2" / subtree / long_fixture.DOC_ID / (scope + suffix)).unlink()

    def test_long_fixture_has_boundary_identifiers_and_nonidentifying_homonym(self):
        source, spans = long_fixture.build_note()
        self.assertNotEqual(len(source), len(source.encode("utf-8")))
        windows = pipeline.fixed_segments(source)
        self.assertEqual(len(windows), 3)
        self.assertNotIn("Dana Kim", windows[0].text)
        self.assertIn("Dana Kim", windows[1].text)
        self.assertNotIn("01/02/2021", windows[1].text)
        self.assertIn("01/02/2021", windows[2].text)
        rows = metrics.score_all(self.pred, self.fixture / "gold")
        row = next(r for r in rows["metrics"] if r["pass"] == "cumulative_pass2"
                   and r["cohort"] == "all_expected" and r["phi_type"] == "ALL")
        self.assertEqual((row["char_tp"], row["char_fp"], row["char_fn"]), (29, 3, 0))
        self.assertAlmostEqual(row["char_precision"], 29 / 32)
        self.assertTrue(self.ops()["coverage_verified"])
        self.assertEqual(self.ops()["expected_segments"], 3)
        self.assertTrue(self.ops()["complete"])

    def test_missing_raw_and_validation_pair_cannot_look_complete(self):
        self.remove_record("raw_responses")
        self.remove_record("validation")
        ops = self.ops()
        self.assertEqual((ops["segments"], ops["expected_segments"]), (2, 3))
        self.assertFalse(ops["coverage_verified"])
        self.assertFalse(ops["complete"])
        rows = metrics.score_all(self.pred, self.fixture / "gold")
        row = next(r for r in rows["metrics"] if r["pass"] == "cumulative_pass2"
                   and r["cohort"] == "all_expected" and r["phi_type"] == "ALL")
        self.assertEqual(row["char_recall"], 1.0)  # saved predictions are unchanged
        self.assertEqual(row["expected_requests"], 3)
        self.assertAlmostEqual(row["finish_stop_rate"], 2 / 3)
        self.assertEqual(row["usable_rate"], 1.0)  # conditional on surviving validations

    def test_missing_validation_preserves_conditional_format_denominator(self):
        self.remove_record("validation")
        ops = self.ops()
        self.assertEqual((ops["validation_records"], ops["usable"]), (2, 2))
        self.assertFalse(ops["complete"])

    def test_legacy_records_without_plan_have_unknown_expected_count(self):
        shutil.rmtree(self.model / "pass_2" / "expected_segments")
        ops = self.ops()
        self.assertIsNone(ops["expected_segments"])
        self.assertFalse(ops["complete"])
        rows = metrics.score_all(self.pred, self.fixture / "gold")
        row = next(r for r in rows["metrics"] if r["pass"] == "cumulative_pass2"
                   and r["cohort"] == "all_expected" and r["phi_type"] == "ALL")
        self.assertIsNone(row["expected_requests"])
        self.assertIsNone(row["finish_stop_rate"])
        self.assertEqual(row["char_recall"], 1.0)

    def test_missing_all_validation_records_does_not_imply_success(self):
        shutil.rmtree(self.model / "pass_2" / "validation")
        self.assertFalse(self.ops()["complete"])
        self.assertFalse(self.ops()["have_validation"])

    def test_unexpected_segment_is_rejected_even_when_record_count_matches(self):
        raw = self.model / "pass_2" / "raw_responses" / long_fixture.DOC_ID
        (raw / "P2-S0002.raw.json").rename(raw / "P2-S0099.raw.json")
        with self.assertRaisesRegex(ValueError, "unexpected segment"):
            self.ops()

    def test_corrupt_plan_is_rejected(self):
        path = self.model / "pass_2" / "expected_segments" / long_fixture.DOC_ID / "manifest.json"
        plan = json.loads(path.read_text())
        plan["segments"].pop()
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "invalid expected-segment manifest"):
            self.ops()

    def test_plan_source_hash_is_checked_against_gold(self):
        path = self.model / "expected_segments" / long_fixture.DOC_ID / "manifest.json"
        plan = json.loads(path.read_text())
        plan["source_sha256"] = "0" * 64
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "invalid expected-segment manifest"):
            metrics.score_all(self.pred, self.fixture / "gold")

    def test_malformed_response_record_prevents_verified_coverage(self):
        path = self.model / "pass_2" / "raw_responses" / long_fixture.DOC_ID / "P2-S0002.raw.json"
        path.write_text("not JSON")
        self.assertFalse(self.ops()["coverage_verified"])

    def test_manifest_records_actual_stub_settings_and_private_permissions(self):
        path = self.model / "inference_manifest.json"
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["backend"], "offline-stub")
        self.assertEqual(manifest["segmentation"]["size"], 3500)
        self.assertEqual(manifest["segmentation"]["overlap"], 400)
        self.assertIsNone(manifest["protocol_file_sha256"])
        self.assertIsNone(manifest["decoding"])
        self.assertIn("run_model.py", manifest["code_sha256"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn("Dana Kim", path.read_text())
        self.assertEqual(list(self.model.rglob("*.txt")), [])

    def test_failed_checkpoint_validation_does_not_remove_previous_output(self):
        marker = self.model / "keep.json"
        marker.write_text("{}")
        with self.assertRaises(FileNotFoundError):
            run_model("stub", self.fixture / "notes", self.pred, ROOT / "protocol",
                      use_stub=True, overwrite_model_output=True,
                      checkpoint_files=[self.root / "missing.gguf"])
        self.assertTrue(marker.exists())

    def test_plan_is_written_before_the_first_request_of_each_pass(self):
        base = self.root / "ordering"
        source, _ = long_fixture.build_note()
        test = self

        class Client:
            calls = 0

            def complete(self, text):
                pass_number = 1 if self.calls < 3 else 2
                directory = base if pass_number == 1 else base / "pass_2"
                test.assertTrue((directory / "expected_segments" / "LONG001" / "manifest.json").is_file())
                self.calls += 1
                return envelope([])

        client = Client()
        process_document(client, "LONG001", source, base)
        self.assertEqual(client.calls, 6)

    def test_second_pass_addition_maps_to_source_and_propagation_is_not_contextual(self):
        source, _ = long_fixture.build_note()
        base = self.root / "staged"

        class Client:
            calls = 0

            def complete(self, text):
                second_pass = self.calls >= 3
                self.calls += 1
                pairs = []
                if second_pass:
                    if "Rae Cole" in text:
                        pairs.append(("Rae Cole", "01_NAME"))
                else:
                    if "Dr. May" in text:
                        pairs.append(("May", "01_NAME"))
                    for literal, label in (("Dana Kim", "01_NAME"), ("01/02/2021", "03_DATE")):
                        if literal in text:
                            pairs.append((literal, label))
                return envelope(pairs)

        process_document(Client(), "LONG001", source, base)
        first = json.loads((base / "resolved/LONG001/resolved_predictions.json").read_text())
        final = json.loads((base / "pass_2/resolved/LONG001/resolved_predictions.json").read_text())
        before = {(p["start"], p["end"]) for p in first["predictions"]}
        after = {(p["start"], p["end"]) for p in final["predictions"]}
        self.assertIn((5700, 5703), before)  # not emitted from this segment's context
        self.assertNotIn((6899, 6907), before)
        self.assertEqual(after - before, {(6899, 6907)})
        self.assertTrue(before <= after)


class ProvenanceUnitTest(unittest.TestCase):
    def test_checkpoint_hashes_include_all_numbered_shards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "example-00001-of-00002.gguf"
            second = root / "example-00002-of-00002.gguf"
            first.write_bytes(b"first")
            with self.assertRaises(FileNotFoundError):
                provenance.checkpoint_records([first])
            second.write_bytes(b"second")
            records = provenance.checkpoint_records([first, second])
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["sha256"], hashlib.sha256(b"first").hexdigest())
            self.assertEqual(records[1]["size_bytes"], 6)

    def test_model_manifest_hashes_protocol_bytes_and_records_supplied_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in provenance.PROTOCOL_FILES:
                (root / name).write_text("synthetic protocol\n", encoding="utf-8")
            data = provenance.inference_manifest(
                model_id="example", protocol_dir=root, segment_size=700, overlap=80,
                use_stub=False, decoding={"temperature": 0, "seed": 42},
                request_timeout=30, strip_content=True,
                server_version="example-build", server_command=["llama-server", "-m", "example.gguf"])
            self.assertEqual(data["protocol_file_sha256"]["system_prompt.txt"],
                             hashlib.sha256(b"synthetic protocol\n").hexdigest())
            self.assertEqual(data["segmentation"]["size"], 700)
            self.assertEqual(data["server"]["version"], "example-build")
            self.assertEqual(data["server"]["command"][1], "-m")

    def test_cli_keeps_server_flags_in_recorded_command(self):
        args = build_parser().parse_args([
            "--model-id", "example", "--notes-dir", "notes", "--out-dir", "out",
            "--checkpoint-file", "weights.gguf", "--server-command", "llama-server",
            "--ctx-size", "8192", "--jinja"])
        self.assertEqual(args.checkpoint_file, [Path("weights.gguf")])
        self.assertEqual(args.server_command, ["llama-server", "--ctx-size", "8192", "--jinja"])


if __name__ == "__main__":
    unittest.main()
