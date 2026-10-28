#!/usr/bin/env python3
"""Reproduce note-level confidence intervals for recall and completeness.

The results table reports a 95% confidence interval for "PHI removed" (character
recall) for every model. That interval is a note-level bootstrap: draw many
samples of the notes with replacement, pool the character counts and recompute
recall in each sample, then take the 2.5th and 97.5th percentiles. This script
reproduces the procedure from the per-note counts the scorer already writes
(``per_doc_long.csv``). Supply the three study executions as three repeated
``--per-doc`` arguments; counts and completeness indicators are averaged by
model, pass, and note before resampling. A single file remains supported for the
synthetic demonstration. The script runs no inference and reads only counts.

Defaults follow the manuscript: 10,000 resamples and seed 20260801 for the
language models (the Presidio comparator used seed 20260721). Each model is
resampled with its own ``random.Random(seed)``, so the result does not depend on
row order and is reproducible from run to run. The interval is a Monte Carlo
estimate, so its endpoints depend on the random-number stream; this standard
library version reproduces the method and, on the study corpus, the manuscript
intervals up to resampling noise. On the synthetic fixture (three notes) it is a
runnable demonstration, not the manuscript's numbers.

Percentiles use the same linear interpolation convention as NumPy's default, so
the endpoints match the tooling the manuscript used.

Standard library only.

Usage::

    python3 scripts/bootstrap_ci.py
    python3 scripts/bootstrap_ci.py --per-doc run1/per_doc_long.csv \
        --per-doc run2/per_doc_long.csv --per-doc run3/per_doc_long.csv
    python3 scripts/bootstrap_ci.py --resamples 10000 --seed 20260801 --out out/bootstrap_ci.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
from collections import OrderedDict
from pathlib import Path


def linear_percentile(values, q):
    """Percentile of ``values`` at ``q`` in [0, 100], NumPy linear convention."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("percentile of an empty sample")
    if n == 1:
        return ordered[0]
    rank = (q / 100.0) * (n - 1)
    low = int(rank)
    high = min(low + 1, n - 1)
    frac = rank - low
    return ordered[low] + frac * (ordered[high] - ordered[low])


def _load_run(per_doc_path, pass_filter):
    """Load one execution, keyed by ``(model, pass, note)``."""
    rows = {}
    with open(per_doc_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pass_ = row["pass_"]
            if pass_filter != "all" and pass_ != pass_filter:
                continue
            key = (row["model_id"], pass_, row["doc"])
            if key in rows:
                raise ValueError(f"duplicate per-note row in {per_doc_path}: {key}")
            try:
                char_tp = float(row["char_tp"])
                gt_phi_chars = float(row["gt_phi_chars"])
                zero_residual = float(row["zero_residual"])
            except (KeyError, ValueError) as exc:
                raise ValueError(f"invalid numeric per-note row in {per_doc_path}: {key}") from exc
            if not all(math.isfinite(value) for value in (char_tp, gt_phi_chars, zero_residual)):
                raise ValueError(f"non-finite per-note value in {per_doc_path}: {key}")
            if not char_tp.is_integer() or not gt_phi_chars.is_integer():
                raise ValueError(f"character counts must be integers in {per_doc_path}: {key}")
            if gt_phi_chars < 0 or char_tp < 0 or char_tp > gt_phi_chars:
                raise ValueError(f"invalid character counts in {per_doc_path}: {key}")
            if zero_residual not in {0.0, 1.0}:
                raise ValueError(f"zero_residual must be 0 or 1 in {per_doc_path}: {key}")
            if zero_residual != float(char_tp == gt_phi_chars):
                raise ValueError(f"zero_residual conflicts with character counts in {per_doc_path}: {key}")
            rows[key] = (char_tp, gt_phi_chars, zero_residual)
    return rows


def load_notes(per_doc_paths, pass_filter):
    """Average matched per-note counts across one or more executions.

    Returns ``(model_id, pass_)`` groups containing tuples of
    ``(mean_char_tp, mean_gt_phi_chars, mean_zero_residual)`` in document order.
    Every execution must contain exactly the same model/pass/note keys, and the
    gold-character count for a note must be identical across executions.
    """
    if isinstance(per_doc_paths, (str, Path)):
        per_doc_paths = [per_doc_paths]
    paths = [Path(path) for path in per_doc_paths]
    if not paths:
        raise ValueError("at least one per-doc file is required")
    resolved_paths = [path.resolve() for path in paths]
    if len(set(resolved_paths)) != len(resolved_paths):
        raise ValueError("duplicate per-doc file supplied as more than one execution")
    runs = [_load_run(path, pass_filter) for path in paths]
    expected = set(runs[0])
    for path, run in zip(paths[1:], runs[1:]):
        if set(run) != expected:
            missing = len(expected - set(run))
            extra = len(set(run) - expected)
            raise ValueError(f"execution key mismatch in {path}: missing={missing}, extra={extra}")

    groups = OrderedDict()
    for model_id, pass_, doc_id in sorted(expected):
        values = [run[(model_id, pass_, doc_id)] for run in runs]
        gt_values = {value[1] for value in values}
        if len(gt_values) != 1:
            raise ValueError(f"gold-character count differs across executions: {(model_id, pass_, doc_id)}")
        n_runs = len(values)
        averaged = tuple(sum(value[index] for value in values) / n_runs for index in range(3))
        groups.setdefault((model_id, pass_), []).append(averaged)
    return groups


def bootstrap_intervals(notes, resamples, seed, alpha):
    """Return point estimates and percentile CIs for recall and completeness."""
    tp_total = sum(tp for tp, _, _ in notes)
    gt_total = sum(gt for _, gt, _ in notes)
    point = tp_total / gt_total if gt_total else None
    complete_point = sum(zero for _, _, zero in notes) / len(notes) if notes else None
    rng = random.Random(seed)
    n = len(notes)
    recalls = []
    completeness = []
    for _ in range(resamples):
        sample = rng.choices(notes, k=n)
        tp_sum = sum(tp for tp, _, _ in sample)
        gt_sum = sum(gt for _, gt, _ in sample)
        if gt_sum:
            recalls.append(tp_sum / gt_sum)
        completeness.append(sum(zero for _, _, zero in sample) / n)
    low = linear_percentile(recalls, 100.0 * (alpha / 2.0))
    high = linear_percentile(recalls, 100.0 * (1.0 - alpha / 2.0))
    complete_low = linear_percentile(completeness, 100.0 * (alpha / 2.0))
    complete_high = linear_percentile(completeness, 100.0 * (1.0 - alpha / 2.0))
    return point, low, high, complete_point, complete_low, complete_high


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--per-doc", action="append", default=None,
        help="per_doc_long.csv for one execution; repeat for all three study executions",
    )
    parser.add_argument(
        "--pass", dest="pass_filter", default="cumulative_pass2",
        choices=["pass1", "cumulative_pass2", "all"],
        help="which pass to summarize (default: %(default)s)",
    )
    parser.add_argument(
        "--resamples", type=int, default=10000,
        help="bootstrap resamples per model (default: %(default)s)",
    )
    parser.add_argument(
        "--seed", type=int, default=20260801,
        help="random seed; the manuscript used 20260801 for models and "
             "20260721 for Presidio (default: %(default)s)",
    )
    parser.add_argument(
        "--alpha", type=float, default=0.05,
        help="two-sided alpha; 0.05 gives a 95%% CI (default: %(default)s)",
    )
    parser.add_argument(
        "--out", default=None,
        help="optional path to write the CI table as CSV",
    )
    args = parser.parse_args()

    if args.resamples <= 0:
        parser.error("--resamples must be positive")
    if not math.isfinite(args.alpha) or not 0 < args.alpha < 1:
        parser.error("--alpha must be finite and between 0 and 1")

    per_doc_paths = [Path(path) for path in (args.per_doc or ["out/per_doc_long.csv"])]
    for per_doc_path in per_doc_paths:
        if not per_doc_path.exists():
            parser.error(f"per-doc file not found: {per_doc_path}")

    try:
        groups = load_notes(per_doc_paths, args.pass_filter)
    except ValueError as exc:
        parser.error(str(exc))
    if not groups:
        parser.error(f"no rows for pass '{args.pass_filter}'")

    rows = []
    for (model_id, pass_), notes in groups.items():
        point, low, high, complete, complete_low, complete_high = bootstrap_intervals(
            notes, args.resamples, args.seed, args.alpha
        )
        rows.append({
            "model_id": model_id,
            "pass": pass_,
            "n_notes": len(notes),
            "char_recall": point,
            "ci_low": low,
            "ci_high": high,
            "complete_note_rate": complete,
            "complete_ci_low": complete_low,
            "complete_ci_high": complete_high,
            "n_runs": len(per_doc_paths),
            "resamples": args.resamples,
            "seed": args.seed,
        })

    header = ["model_id", "pass", "n_notes", "n_runs", "char_recall", "ci_low", "ci_high",
              "complete_note_rate", "complete_ci_low", "complete_ci_high", "resamples", "seed"]
    if args.out:
        output_path = Path(args.out)
        if output_path.is_symlink():
            parser.error(f"refusing symlinked output file: {output_path}")
        output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        output_path.chmod(0o600)
        print(f"wrote {len(rows)} rows to {output_path}")

    width = max(len(row["model_id"]) for row in rows)
    conf = int(round((1.0 - args.alpha) * 100))
    print(f"{'model':<{width}}  {'pass':<16}  runs  notes  recall   {conf}% CI       complete   {conf}% CI")
    for row in rows:
        recall = "n/a" if row["char_recall"] is None else f"{row['char_recall']:.3f}"
        complete = "n/a" if row["complete_note_rate"] is None else f"{row['complete_note_rate']:.3f}"
        print(f"{row['model_id']:<{width}}  {row['pass']:<16}  "
              f"{row['n_runs']:>4}  {row['n_notes']:>5}  {recall:>6}   "
              f"({row['ci_low']:.3f}, {row['ci_high']:.3f})  {complete:>8}   "
              f"({row['complete_ci_low']:.3f}, {row['complete_ci_high']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
