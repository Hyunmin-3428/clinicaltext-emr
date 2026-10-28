"""End-to-end and unit tests over the synthetic fixture.

These run entirely offline with the deterministic test stub -- no GPU, no
model weights, no PHI, and no third-party packages. The expected metric values
below are derived from the synthetic gold and are hand-checkable (see the
per-type breakdown in the README). Run them from the repository root with::

    python3 -m unittest discover -s tests

The whole stub pipeline (segment -> extract -> resolve -> two-pass redact ->
score) executes in well under a second.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from xml.dom import minidom

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from deid import metrics, pipeline  # noqa: E402
from deid.run_model import run_model  # noqa: E402
import make_checkpoints_table  # noqa: E402
import make_corpus_table  # noqa: E402
import make_figures  # noqa: E402

FIXTURE = REPO_ROOT / "fixtures" / "synthetic"
NOTES_DIR = FIXTURE / "notes"
GOLD_DIR = FIXTURE / "gold"
NAMES = [line.strip() for line in (FIXTURE / "name_gazetteer.txt").read_text().splitlines() if line.strip()]


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


class FixtureMetricsTest(unittest.TestCase):
    """Score the offline test stub over the fixtures once, then assert the tables."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        work = Path(cls._tmp.name)
        pred_dir = work / "predictions"
        out_dir = work / "out"
        run_model("offline-stub", NOTES_DIR, pred_dir, REPO_ROOT / "protocol",
                  use_stub=True, name_gazetteer=NAMES)
        results = metrics.score_all(pred_dir, GOLD_DIR)
        metrics.write_outputs(out_dir, GOLD_DIR, results)
        cls.metrics_rows = read_rows(out_dir / "metrics_long.csv")
        cls.per_doc_rows = read_rows(out_dir / "per_doc_long.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def all_row(self, pass_label: str, cohort: str) -> dict[str, str]:
        for row in self.metrics_rows:
            if row["phi_type"] == "ALL" and row["pass"] == pass_label and row["cohort"] == cohort:
                return row
        self.fail(f"no ALL row for {pass_label}/{cohort}")

    def test_headline_metrics_match_fixture(self) -> None:
        row = self.all_row("cumulative_pass2", "all_expected")
        # Character confusion: 335 gold PHI chars, 82 leaked (all geographic).
        self.assertEqual(int(row["gt_phi_chars"]), 335)
        self.assertEqual(int(row["char_tp"]), 253)
        self.assertEqual(int(row["char_fp"]), 0)
        self.assertEqual(int(row["char_fn"]), 82)
        self.assertAlmostEqual(float(row["char_recall"]), 253 / 335)
        # The stub only ever emits grounded literals, so it never over-redacts.
        self.assertEqual(float(row["char_precision"]), 1.0)
        self.assertEqual(float(row["over_redaction_rate"]), 0.0)
        # One of three notes (TEST002) is fully clean.
        self.assertAlmostEqual(float(row["zero_residual_note_rate"]), 1 / 3)

    def test_geographic_is_the_only_leak(self) -> None:
        per_type = {r["phi_type"]: r for r in self.metrics_rows
                    if r["pass"] == "cumulative_pass2" and r["cohort"] == "all_expected"
                    and r["phi_type"] != "ALL"}
        # Everything the stub targets is fully recalled...
        for code in ("01_NAME", "03_DATE", "04_AGE_OVER_89", "05_TELEPHONE_NUMBER",
                     "07_EMAIL_ADDRESS", "09_MEDICAL_RECORD_NUMBER"):
            self.assertEqual(float(per_type[code]["char_recall"]), 1.0, code)
        # ...and geographic subdivisions (which it does not target) are all missed.
        self.assertEqual(float(per_type["02_GEOGRAPHIC_SUBDIVISION"]["char_recall"]), 0.0)

    def test_per_note_residuals(self) -> None:
        per_doc = {(r["doc"], r["pass_"]): r for r in self.per_doc_rows}
        self.assertEqual(int(per_doc[("TEST002", "cumulative_pass2")]["residual_phi_chars"]), 0)
        self.assertEqual(int(per_doc[("TEST002", "cumulative_pass2")]["zero_residual"]), 1)
        self.assertEqual(int(per_doc[("TEST001", "cumulative_pass2")]["residual_phi_chars"]), 58)
        self.assertEqual(int(per_doc[("TEST003", "cumulative_pass2")]["residual_phi_chars"]), 24)


class PipelineUnitTest(unittest.TestCase):
    """Invariants of the resolver/redaction engine, independent of any model."""

    def test_confusion_counts_partition_the_document(self) -> None:
        # tp + fp + fn + tn must equal the document length for every scored note.
        for doc_id in ("TEST001", "TEST002", "TEST003"):
            gold = metrics.load_gold(GOLD_DIR / f"{doc_id}.json")
            score = metrics.score_document(gold, [])  # empty predictions -> all gold is FN
            self.assertEqual(score["tp"] + score["fp"] + score["fn"] + score["tn"], score["doc_chars"])
            self.assertEqual(score["tp"], 0)
            self.assertEqual(score["fn"], score["gt"])

    def test_segmentation_covers_text_without_gaps(self) -> None:
        text = "x" * 9000
        segments = pipeline.fixed_segments(text, size=3500, overlap=400)
        self.assertEqual(segments[0].start, 0)
        self.assertEqual(segments[-1].end, len(text))
        # Adjacent windows overlap by exactly the configured amount.
        for left, right in zip(segments, segments[1:]):
            self.assertEqual(left.end - right.start, 400)

    def test_resolution_propagates_and_maps_coordinates(self) -> None:
        source = "Seen by Dana Kim. Dana Kim signed the note."
        pairs = [pipeline.Pair("Dana Kim", "01_NAME")]
        predictions = pipeline.resolve_pairs(source, pipeline.identity_map(source), pairs)
        # The literal appears twice; both occurrences are grounded to source offsets.
        self.assertEqual(len(predictions), 2)
        self.assertTrue(all(source[p.start:p.end] == "Dana Kim" for p in predictions))

    def test_pass2_match_across_marker_is_dropped(self) -> None:
        # After Pass 1 redacts [8, 11), a Pass-2 match spanning the marker cannot be
        # mapped back to contiguous source coordinates and must be discarded.
        source = "abcdefgh123ijklmnop"
        representation, coordinate_map = pipeline.redacted_representation_with_map(source, [(8, 11)])
        self.assertIn(pipeline.REDACTION_MARKER, representation)
        spanning = pipeline.Pair(representation[6:14], "01_NAME")  # straddles the marker
        self.assertEqual(pipeline.resolve_pairs(representation, coordinate_map, [spanning]), [])


class ReproductionScriptsTest(unittest.TestCase):
    """The manuscript-table generators reproduce the fixture's known counts."""

    def test_corpus_table_matches_fixture(self) -> None:
        summary = make_corpus_table.summarize(GOLD_DIR)
        self.assertEqual(summary["n_notes"], 3)
        self.assertEqual(summary["total_spans"], 26)
        self.assertEqual(summary["total_phi_chars"], 335)
        # Seven Safe Harbor categories occur in the fixture.
        self.assertEqual(len(summary["per_type_spans"]), 7)
        # The per-category rows partition the totals exactly.
        rows = make_corpus_table.panel_b(summary)
        self.assertEqual(sum(spans for _code, spans, _share, _chars in rows), 26)
        self.assertEqual(sum(chars for _code, _spans, _share, chars in rows), 335)
        # Geographic subdivisions are the stub's known leak: 82 characters.
        geo = {code: chars for code, _spans, _share, chars in rows}
        self.assertEqual(geo["02_GEOGRAPHIC_SUBDIVISION"], 82)

    def test_checkpoints_table_lists_every_model(self) -> None:
        records = make_checkpoints_table.build_rows(make_checkpoints_table.load_models())
        self.assertEqual(len(records), 17)
        # Every model resolves to a Hugging Face repository.
        self.assertTrue(all(r["repo"] for r in records))
        by_id = {r["model_id"]: r for r in records}
        top = by_id["gemma-4-31B-it"]
        self.assertEqual(top["model"], "Gemma-4 31B")
        self.assertEqual(top["size_gb"], 21.66)
        self.assertEqual(top["arch"], "dense")


class FigureGenerationTest(unittest.TestCase):
    """scripts/make_figures.py renders every result figure from the scorer output."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        work = Path(cls._tmp.name)
        out_dir = work / "out"
        cls.fig_dir = work / "figures"
        run_model("offline-stub", NOTES_DIR, work / "predictions", REPO_ROOT / "protocol",
                  use_stub=True, name_gazetteer=NAMES)
        results = metrics.score_all(work / "predictions", GOLD_DIR)
        metrics.write_outputs(out_dir, GOLD_DIR, results)
        cls.metrics_path = out_dir / "metrics_long.csv"
        exit_code = make_figures.main(["--metrics", str(cls.metrics_path),
                                       "--out-dir", str(cls.fig_dir)])
        assert exit_code == 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def figure_row(self, stem: str) -> dict[str, str]:
        return {r["model_id"]: r for r in read_rows(self.fig_dir / f"{stem}.csv")}["offline-stub"]

    def test_all_figures_are_written(self) -> None:
        for stem in ("figure2", "figure3", "figure4", "figure5", "figure6"):
            self.assertTrue((self.fig_dir / f"{stem}.svg").exists(), f"{stem}.svg")
            self.assertTrue((self.fig_dir / f"{stem}.csv").exists(), f"{stem}.csv")

    def test_every_svg_is_well_formed(self) -> None:
        # Each figure must parse as XML and actually draw bars/points.
        for svg in sorted(self.fig_dir.glob("*.svg")):
            document = minidom.parseString(svg.read_text(encoding="utf-8"))
            drawn = document.getElementsByTagName("rect") + document.getElementsByTagName("circle")
            self.assertGreater(len(drawn), 0, svg.name)

    def test_figure5_added_by_pass2_is_consistent(self) -> None:
        row = self.figure_row("figure5")
        gain = float(row["char_recall_cumulative_pass2"]) - float(row["char_recall_pass1"])
        self.assertAlmostEqual(float(row["added_by_pass2"]), gain, places=6)
        # The stub is identical across passes, so the second pass adds nothing.
        self.assertAlmostEqual(float(row["added_by_pass2"]), 0.0, places=6)

    def test_figure6_matches_known_per_category_recall(self) -> None:
        row = self.figure_row("figure6")
        for category in ("date", "name", "telephone"):
            self.assertAlmostEqual(float(row[category]), 1.0, msg=category)
        # Geographic subdivisions are the stub's only leak; overall = 253/335.
        self.assertAlmostEqual(float(row["geographic"]), 0.0)
        self.assertAlmostEqual(float(row["overall"]), 253 / 335)

    def test_multiple_metric_executions_are_averaged_before_plotting(self) -> None:
        second_metrics = Path(self._tmp.name) / "second_metrics.csv"
        rows = read_rows(self.metrics_path)
        original = next(
            row for row in rows
            if row["pass"] == "cumulative_pass2" and row["cohort"] == "all_expected"
            and row["phi_type"] == "ALL"
        )
        original_recall = float(original["char_recall"])
        second_recall = 0.5
        for row in rows:
            if (row["pass"] == "cumulative_pass2" and row["cohort"] == "all_expected"
                    and row["phi_type"] == "ALL"):
                row["char_recall"] = str(second_recall)
        with second_metrics.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        averaged_dir = Path(self._tmp.name) / "averaged_figures"
        exit_code = make_figures.main([
            "--metrics", str(self.metrics_path), "--metrics", str(second_metrics),
            "--out-dir", str(averaged_dir),
        ])
        self.assertEqual(exit_code, 0)
        averaged_row = read_rows(averaged_dir / "figure2.csv")[0]
        expected = (original_recall + second_recall) / 2
        self.assertAlmostEqual(float(averaged_row["char_recall"]), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
