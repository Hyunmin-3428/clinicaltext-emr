#!/usr/bin/env python3
"""Rebuild the manuscript's model-checkpoint table (Table 2) from the repository.

The evaluated checkpoints and their metadata live in
``model_configs/build_configs.py`` -- the single source of truth that also writes
the per-model ``model_config.json`` files the scorer reads. This script renders
that roster as the checkpoint table the manuscript reports: one row per model with
its developer, parameters, architecture, medical flag, quantization, on-disk size,
and Hugging Face repository, in the manuscript's results-table order.

It needs no data beyond the repository, so it reproduces the manuscript's Table 2
offline. The display names match the manuscript's ``Model`` column; every other
value is read straight from the model configs, so the table cannot drift from the
metadata the scorer actually uses.

Standard library only.

Usage::

    python3 scripts/make_checkpoints_table.py
    python3 scripts/make_checkpoints_table.py --csv out/checkpoints.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_CONFIGS = REPO_ROOT / "model_configs" / "build_configs.py"

# Manuscript display label per run label (the model_configs key / --model-id).
# These are the names in the manuscript's checkpoint table; all other columns are
# read from the configs. A model without an entry here falls back to its run label.
DISPLAY_NAMES = {
    "gemma-4-31B-it": "Gemma-4 31B",
    "gemma-4-12b-it": "Gemma-4 12B",
    "Mistral-Small-3.2-24B-Instruct-2506": "Mistral-Small 24B",
    "gemma-4-E4B-it": "Gemma-4 E4B",
    "gemma-4-26B-A4B-it": "Gemma-4 26B-A4B",
    "Ministral-3-14B-Instruct-2512": "Ministral-3 14B",
    "gemma-4-E2B-it": "Gemma-4 E2B",
    "gemma-3-27b-it": "Gemma-3 27B",
    "phi-4": "Phi-4 14B",
    "medgemma-27b-text-it": "MedGemma 27B",
    "granite-4.1-30b": "Granite-4.1 30B",
    "granite-4.1-8b": "Granite-4.1 8B",
    "gemma-3-4b-it": "Gemma-3 4B",
    "Meta-Llama-3.1-8B-Instruct": "Llama-3.1 8B",
    "Olmo-3-7B-Instruct": "OLMo-3 7B",
    "gemma-3-1b-it": "Gemma-3 1B",
    "medgemma-1.5-4b-it": "MedGemma-1.5 4B",
}


def load_models():
    """Import the ordered ``MODELS`` roster from model_configs/build_configs.py."""
    spec = importlib.util.spec_from_file_location("build_configs", BUILD_CONFIGS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MODELS


def format_params(cfg) -> str:
    """Render dense, total/effective, or total/active parameter counts."""
    total = "{:.1f}".format(cfg["params_total_B"])
    if cfg.get("params_effective_B") is not None:
        return "{}/{:.1f}".format(total, cfg["params_effective_B"])
    if cfg.get("moe") and cfg.get("params_active_B") is not None:
        return "{}/{:.1f}".format(total, cfg["params_active_B"])
    return total


def build_rows(models):
    """One record per model, in roster order, with rendered display fields."""
    records = []
    for model_id, cfg in models.items():
        repo = cfg.get("hf_gguf_repo", "") or ""
        records.append({
            "model_id": model_id,
            "model": DISPLAY_NAMES.get(model_id, model_id),
            "developer": cfg.get("developer", ""),
            "params": format_params(cfg),
            "arch": "MoE" if cfg.get("moe") else "dense",
            "medical": "yes" if cfg.get("is_medical") else "",
            "quant": cfg.get("quant_target", ""),
            "size_gb": cfg.get("size_gb"),
            "repo": repo,
        })
    return records


def print_markdown(records) -> None:
    print("Model checkpoints (manuscript Table 2); {} models".format(len(records)))
    print()
    header = ["Model", "Developer", "Params (B)", "Arch.", "Medical", "Quant",
              "Size (GB)", "Weights (Hugging Face)"]
    align = ["---", "---", "---", "---", ":---:", "---", "--:", "---"]
    print("| " + " | ".join(header) + " |")
    print("| " + " | ".join(align) + " |")
    for r in records:
        size = "" if r["size_gb"] is None else "{:.2f}".format(r["size_gb"])
        weights = ("[`{0}`](https://huggingface.co/{0})".format(r["repo"])
                   if r["repo"] else "")
        medical = "✓" if r["medical"] else ""
        print("| " + " | ".join([
            r["model"], r["developer"], r["params"], r["arch"], medical,
            r["quant"], size, weights,
        ]) + " |")


def write_csv(path, records) -> None:
    fields = ["model_id", "model", "developer", "params", "arch", "medical",
              "quant", "size_gb", "repo"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", default=None,
                        help="also write the table as CSV to this path")
    args = parser.parse_args()

    records = build_rows(load_models())
    print_markdown(records)
    if args.csv:
        write_csv(args.csv, records)
        print("\nwrote {} rows to {}".format(len(records), args.csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
