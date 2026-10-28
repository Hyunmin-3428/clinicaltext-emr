#!/usr/bin/env python3
"""Render result figures from ``metrics_long.csv``.

These are the repository's own charts of the study's result analyses -- the same
quantities the manuscript presents as its Figures 2 to 6, drawn from whatever run
you scored. They are illustrative renders in a simple built-in style, not copies
of the manuscript's published figures (which span all seventeen models on the
real corpus and look different).

For each figure this writes two files into the output directory:

* ``figureN.csv`` -- the exact table plotted, so the numbers can be checked or
  re-plotted in any tool; and
* ``figureN.svg`` -- a self-contained vector chart (no embedded fonts or
  scripts) that opens in any browser and renders inline on GitHub.

The figures use the same view the paper reports: the cumulative two-pass
predictions over every expected note (``pass == cumulative_pass2``,
``cohort == all_expected``, note-wide ``phi_type == ALL``), with models ordered
by character recall. Supply ``--metrics`` once per execution to reproduce the
three-run means; files are key-matched and numeric outcomes averaged before
plotting. ``figure5`` also reads the ``pass1`` rows to show the second-pass gain,
and ``figure6`` reads the per-category rows.

    python scripts/make_figures.py --metrics out/metrics_long.csv --out-dir out/figures

Standard library only; no plotting dependencies.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
from typing import Optional

from metric_runs import load_averaged_metrics

# A small, print-safe palette (colour-blind friendly, ASCII hex only).
PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756",
           "#72B7B2", "#B279A2", "#EECA3B", "#FF9DA6"]

# Identifier categories shown in figure6, with short axis labels.
CATEGORIES = [
    ("03_DATE", "Date"),
    ("01_NAME", "Name"),
    ("05_TELEPHONE_NUMBER", "Telephone"),
    ("02_GEOGRAPHIC_SUBDIVISION", "Geographic"),
    ("ALL", "Overall"),
]


# --- reading metrics --------------------------------------------------------

def num(row: dict[str, str], field: str) -> Optional[float]:
    """Read one cell as a float, or ``None`` if blank/undefined."""
    value = row.get(field, "")
    if value in ("", "None"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def rows_by_model(rows: list[dict[str, str]], pass_label: str,
                  phi_type: str = "ALL", cohort: str = "all_expected") -> dict[str, dict[str, str]]:
    """Index the rows matching one pass/cohort/type by model id."""
    return {r["model_id"]: r for r in rows
            if r["pass"] == pass_label and r["phi_type"] == phi_type and r["cohort"] == cohort}


def model_order(rows: list[dict[str, str]]) -> list[str]:
    """Model ids ranked by descending character recall (the results-table order)."""
    base = [r for r in rows
            if r["phi_type"] == "ALL" and r["pass"] == "cumulative_pass2"
            and r["cohort"] == "all_expected"]
    base.sort(key=lambda r: num(r, "char_recall") if num(r, "char_recall") is not None else -1.0,
              reverse=True)
    return [r["model_id"] for r in base]


# --- SVG primitives ---------------------------------------------------------

def _f(value: float) -> str:
    """Format a coordinate (two decimals is plenty at screen resolution)."""
    return f"{float(value):.2f}"


def _esc(text: object) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _open(width: int, height: int) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" font-family="Helvetica,Arial,sans-serif">'
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>')


def _text(x: float, y: float, s: object, size: float = 12, anchor: str = "start",
          fill: str = "#222222", weight: str = "normal",
          rotate: Optional[tuple[float, float, float]] = None) -> str:
    attrs = (f'x="{_f(x)}" y="{_f(y)}" font-size="{_f(size)}" '
             f'text-anchor="{anchor}" fill="{fill}"')
    if weight != "normal":
        attrs += f' font-weight="{weight}"'
    if rotate is not None:
        deg, rx, ry = rotate
        attrs += f' transform="rotate({_f(deg)} {_f(rx)} {_f(ry)})"'
    return f'<text {attrs}>{_esc(s)}</text>'


def _rect(x: float, y: float, w: float, h: float, fill: str) -> str:
    return (f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
            f'rx="2" fill="{fill}"/>')


def _line(x1: float, y1: float, x2: float, y2: float,
          stroke: str = "#333333", width: float = 1.0) -> str:
    return (f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" '
            f'stroke="{stroke}" stroke-width="{_f(width)}"/>')


def _circle(cx: float, cy: float, r: float, fill: str) -> str:
    return (f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r)}" fill="{fill}" '
            f'stroke="#FFFFFF" stroke-width="1.5"/>')


# --- charts -----------------------------------------------------------------

def grouped_bar_svg(title: str, x_labels: list[str],
                    series: list[tuple[str, list[Optional[float]], str]],
                    y_label: str, y_max: float = 1.0) -> str:
    """Grouped vertical bar chart: one group per x label, one bar per series."""
    height = 460
    left, top, bottom = 72, 54, 104
    plot_w = 508  # fixed plot width; the canvas grows to fit the legend labels
    px0, py0 = left, top
    px1, py1 = px0 + plot_w, height - bottom
    plot_h = py1 - py0
    # Grow the canvas so the right-hand legend never clips, whether the labels are
    # long model ids or the two-pass series names. Reserve a conservative
    # 8.5 px/char at font-size 12, an upper bound for mixed-case Helvetica.
    label_px = max((len(name) for name, _, _ in series), default=0) * 8.5
    width = max(780, int(math.ceil(px1 + 20 + 19 + label_px + 24)))

    parts = [_open(width, height),
             _text(width / 2, 30, title, size=17, anchor="middle", weight="bold")]

    # horizontal gridlines and y-axis ticks (0, 0.2, ... 1.0 for a rate axis)
    for k in range(6):
        t = y_max * k / 5
        y = py1 - (t / y_max) * plot_h
        parts.append(_line(px0, y, px1, y, stroke="#E8E8E8"))
        parts.append(_text(px0 - 8, y + 4, f"{t:.1f}", size=11, anchor="end", fill="#555555"))
    parts.append(_text(20, (py0 + py1) / 2, y_label, size=12, anchor="middle",
                       fill="#555555", rotate=(-90, 20, (py0 + py1) / 2)))

    # axes
    parts.append(_line(px0, py0, px0, py1, stroke="#333333"))
    parts.append(_line(px0, py1, px1, py1, stroke="#333333"))

    n_slots = max(len(x_labels), 1)
    n_series = max(len(series), 1)
    slot_w = plot_w / n_slots
    group_w = slot_w * 0.8
    bar_w = group_w / n_series
    show_values = n_slots * n_series <= 16
    rotate_x = max((len(s) for s in x_labels), default=0) > 10

    for i, label in enumerate(x_labels):
        cx = px0 + slot_w * (i + 0.5)
        gx0 = cx - group_w / 2
        for j, (_, values, color) in enumerate(series):
            v = values[i]
            bx = gx0 + j * bar_w
            if v is None:
                parts.append(_text(bx + bar_w / 2, py1 - 3, "n/a", size=9,
                                   anchor="middle", fill="#AAAAAA"))
                continue
            bh = max((v / y_max) * plot_h, 0.0)
            parts.append(_rect(bx, py1 - bh, bar_w * 0.9, bh, color))
            if show_values:
                parts.append(_text(bx + bar_w * 0.45, py1 - bh - 4, f"{v:.3f}",
                                   size=9, anchor="middle", fill="#333333"))
        if rotate_x:
            parts.append(_text(cx, py1 + 16, label, size=11, anchor="end",
                               rotate=(-30, cx, py1 + 16)))
        else:
            parts.append(_text(cx, py1 + 18, label, size=11, anchor="middle"))

    # legend, stacked on the right
    lx, ly = px1 + 20, py0 + 8
    for j, (name, _, color) in enumerate(series):
        yy = ly + j * 22
        parts.append(_rect(lx, yy, 13, 13, color))
        parts.append(_text(lx + 19, yy + 11, name, size=12, fill="#222222"))

    parts.append("</svg>")
    return "\n".join(parts)


def scatter_svg(title: str, points: list[tuple[Optional[float], Optional[float], str]],
                x_label: str, y_label: str, y_max: float = 1.0) -> str:
    """Labelled scatter of ``(x, y)`` points, one per model."""
    width, height = 780, 460
    left, right, top, bottom = 72, 44, 54, 70
    px0, py0, px1, py1 = left, top, width - right, height - bottom
    plot_w, plot_h = px1 - px0, py1 - py0

    xs = [p[0] for p in points if p[0] is not None]
    x_max = max(max(xs) * 1.25, 0.05) if xs else 1.0

    parts = [_open(width, height),
             _text(width / 2, 30, title, size=17, anchor="middle", weight="bold")]

    # gridlines + ticks
    for k in range(6):
        yt = y_max * k / 5
        y = py1 - (yt / y_max) * plot_h
        parts.append(_line(px0, y, px1, y, stroke="#E8E8E8"))
        parts.append(_text(px0 - 8, y + 4, f"{yt:.1f}", size=11, anchor="end", fill="#555555"))
        xt = x_max * k / 5
        x = px0 + (xt / x_max) * plot_w
        parts.append(_line(x, py0, x, py1, stroke="#F1F1F1"))
        parts.append(_text(x, py1 + 16, f"{xt:.3f}", size=10, anchor="middle", fill="#555555"))

    parts.append(_line(px0, py0, px0, py1, stroke="#333333"))
    parts.append(_line(px0, py1, px1, py1, stroke="#333333"))
    parts.append(_text(20, (py0 + py1) / 2, y_label, size=12, anchor="middle",
                       fill="#555555", rotate=(-90, 20, (py0 + py1) / 2)))
    parts.append(_text((px0 + px1) / 2, height - 24, x_label, size=12,
                       anchor="middle", fill="#555555"))

    for i, (x, y, label) in enumerate(points):
        if x is None or y is None:
            continue
        cx = px0 + (x / x_max) * plot_w
        cy = py1 - (y / y_max) * plot_h
        color = PALETTE[i % len(PALETTE)]
        parts.append(_circle(cx, cy, 6, color))
        if cx > px1 - 90:
            parts.append(_text(cx - 10, cy + 4, label, size=11, anchor="end", fill="#222222"))
        else:
            parts.append(_text(cx + 10, cy + 4, label, size=11, anchor="start", fill="#222222"))

    parts.append("</svg>")
    return "\n".join(parts)


# --- figure builders --------------------------------------------------------
# Each returns (csv_fieldnames, csv_rows, svg_string).

def build_figure2(rows, order, title):
    p2 = rows_by_model(rows, "cumulative_pass2")
    fields = ["model_id", "char_recall", "zero_residual_note_rate"]
    data = [{"model_id": m,
             "char_recall": p2[m].get("char_recall", ""),
             "zero_residual_note_rate": p2[m].get("zero_residual_note_rate", "")}
            for m in order if m in p2]
    series = [
        ("PHI removed (char recall)", [num(p2[m], "char_recall") if m in p2 else None for m in order], PALETTE[0]),
        ("Notes fully cleaned", [num(p2[m], "zero_residual_note_rate") if m in p2 else None for m in order], PALETTE[1]),
    ]
    return fields, data, grouped_bar_svg(title, order, series, "proportion")


def build_figure3(rows, order, title):
    p2 = rows_by_model(rows, "cumulative_pass2")
    fields = ["model_id", "over_redaction_rate", "char_recall"]
    data = [{"model_id": m,
             "over_redaction_rate": p2[m].get("over_redaction_rate", ""),
             "char_recall": p2[m].get("char_recall", "")}
            for m in order if m in p2]
    points = [(num(p2[m], "over_redaction_rate"), num(p2[m], "char_recall"), m)
              for m in order if m in p2]
    svg = scatter_svg(title, points,
                      "share of redacted characters outside reference spans",
                      "character recall (reference-identifier removal)")
    return fields, data, svg


def build_figure4(rows, order, title):
    p2 = rows_by_model(rows, "cumulative_pass2")
    cols = [("any_span_recall", "Any"), ("full_span_recall", "Full"),
            ("relaxed_span_recall", "Relaxed (2-char)"), ("strict_span_recall", "Strict")]
    fields = ["model_id"] + [c for c, _ in cols]
    data = [dict({"model_id": m}, **{c: p2[m].get(c, "") for c, _ in cols})
            for m in order if m in p2]
    series = [(label, [num(p2[m], c) if m in p2 else None for m in order], PALETTE[k])
              for k, (c, label) in enumerate(cols)]
    return fields, data, grouped_bar_svg(title, order, series, "span recall")


def build_figure5(rows, order, title):
    p1 = rows_by_model(rows, "pass1")
    p2 = rows_by_model(rows, "cumulative_pass2")
    fields = ["model_id", "char_recall_pass1", "char_recall_cumulative_pass2", "added_by_pass2"]
    data = []
    for m in order:
        r1 = num(p1[m], "char_recall") if m in p1 else None
        r2 = num(p2[m], "char_recall") if m in p2 else None
        added = r2 - r1 if (r1 is not None and r2 is not None) else None
        data.append({
            "model_id": m,
            "char_recall_pass1": p1[m].get("char_recall", "") if m in p1 else "",
            "char_recall_cumulative_pass2": p2[m].get("char_recall", "") if m in p2 else "",
            "added_by_pass2": "" if added is None else f"{added:.6f}",
        })
    series = [
        ("Pass 1", [num(p1[m], "char_recall") if m in p1 else None for m in order], PALETTE[0]),
        ("Cumulative (P1 + P2)", [num(p2[m], "char_recall") if m in p2 else None for m in order], PALETTE[2]),
    ]
    return fields, data, grouped_bar_svg(title, order, series, "char recall")


def build_figure6(rows, order, title):
    lut = {(r["model_id"], r["phi_type"]): r for r in rows
           if r["pass"] == "cumulative_pass2" and r["cohort"] == "all_expected"}
    fields = ["model_id"] + [label.lower() for _, label in CATEGORIES]
    data = []
    for m in order:
        entry = {"model_id": m}
        for code, label in CATEGORIES:
            cell = lut.get((m, code))
            entry[label.lower()] = cell.get("char_recall", "") if cell else ""
        data.append(entry)
    x_labels = [label for _, label in CATEGORIES]
    series = [(m, [num(lut[(m, code)], "char_recall") if (m, code) in lut else None
                   for code, _ in CATEGORIES], PALETTE[k % len(PALETTE)])
              for k, m in enumerate(order)]
    return fields, data, grouped_bar_svg(title, x_labels, series, "char recall")


FIGURES = [
    ("figure2", "PHI removed and notes fully cleaned", build_figure2),
    ("figure3", "Reference-identifier removal vs over-redaction", build_figure3),
    ("figure4", "Span coverage and boundary agreement", build_figure4),
    ("figure5", "Second-pass gain in PHI removed", build_figure5),
    ("figure6", "PHI removed by identifier category", build_figure6),
]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    path.chmod(0o600)


def main(argv: Optional[list[str]] = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metrics", type=Path, action="append", default=None,
                        help="metrics_long.csv for one execution; repeat for all three study executions")
    parser.add_argument("--out-dir", type=Path, default=Path("out/figures"),
                        help="directory for the generated figures (default: out/figures)")
    args = parser.parse_args(argv)

    metrics_paths = args.metrics or [Path("out/metrics_long.csv")]
    for metrics_path in metrics_paths:
        if not metrics_path.exists():
            parser.error(f"metrics file not found: {metrics_path}")
    try:
        rows = load_averaged_metrics(metrics_paths)
    except ValueError as exc:
        parser.error(str(exc))

    order = model_order(rows)
    if not order:
        parser.error("no cumulative_pass2 / all_expected / ALL rows")

    if args.out_dir.is_symlink():
        parser.error(f"refusing symlinked output directory: {args.out_dir}")
    args.out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.out_dir.chmod(0o700)
    print(f"{len(order)} model(s), {len(metrics_paths)} execution(s), "
          f"ranked by character recall: {', '.join(order)}")
    print(f"writing figures -> {args.out_dir}")
    for stem, title, builder in FIGURES:
        fields, data, svg = builder(rows, order, title)
        write_csv(args.out_dir / f"{stem}.csv", fields, data)
        svg_path = args.out_dir / f"{stem}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        svg_path.chmod(0o600)
        print(f"  {stem}.svg   {stem}.csv   ({len(data)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
