#!/usr/bin/env python3
"""Generate the per-model ``model_config.json`` metadata files.

The scorer (``deid.metrics.load_config``) reads an optional ``model_config.json``
from each model's output directory and carries its fields into
``metrics_long.csv`` and ``model_manifest.csv``: developer, parameter counts, the
MoE / medical / reasoning flags, quantization, and the Hugging Face repository
(from which it derives the weights URL). This script is the single source of
truth for that metadata; it writes one ``<model-id>.json`` per evaluated model,
keyed by the run label you pass to ``deid.run_model --model-id``.

Run it from the repository root to (re)generate the files::

    python model_configs/build_configs.py

Point the runner at one with ``--model-config model_configs/<model-id>.json``;
the end-to-end script resolves the matching file automatically from ``--model-id``.

Standard library only.
"""

from __future__ import annotations

import json
from pathlib import Path

# One entry per evaluated model, keyed by run label and ordered as in the
# manuscript's results table. The fields are exactly those consumed by
# deid.metrics.load_config, plus size_gb -- the on-disk checkpoint size from the
# manuscript's checkpoint table, recorded here for provenance (the scorer does
# not read it). ``params_effective_B`` is used for Gemma-4 E2B/E4B, whose
# developer model card distinguishes total parameters including per-layer
# embeddings from the smaller effective parameter count. ``params_active_B`` is
# used only for mixture-of-experts models.
#
# hf_gguf_repo points at the resolving, runnable GGUF repository the study used to
# obtain the quant named in quant_target. For three models the manuscript's
# checkpoint table cites an upstream or differently named source; the repository
# keeps the working GGUF id instead, and the difference is documented in
# docs/MANUSCRIPT_MAP.md:
#   - Ministral-3-14B: manuscript cites mistralai/Ministral-3-14B-Instruct-2512
#     (safetensors only); the evaluated Q6_K checkpoint is the unsloth GGUF mirror.
#   - Granite-4.1 30B / 8B: manuscript cites the ...-instruct-GGUF ids; the
#     ...-GGUF ids used here resolve and carry the matching Q5_K_M / Q6_K quants.
MODELS = {
    "gemma-4-31B-it": {
        "developer": "Google", "family": "Gemma-4", "params_total_B": 30.7,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q5_K_M", "size_gb": 21.66,
        "hf_gguf_repo": "unsloth/gemma-4-31B-it-GGUF",
    },
    "gemma-4-12b-it": {
        "developer": "Google", "family": "Gemma-4", "params_total_B": 12.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q8_0", "size_gb": 12.67,
        "hf_gguf_repo": "unsloth/gemma-4-12b-it-GGUF",
    },
    "Mistral-Small-3.2-24B-Instruct-2506": {
        "developer": "Mistral AI", "family": "Mistral-Small", "params_total_B": 24.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q5_K_M", "size_gb": 16.76,
        "hf_gguf_repo": "unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF",
    },
    "gemma-4-E4B-it": {
        "developer": "Google", "family": "Gemma-4", "params_total_B": 8.0,
        "params_effective_B": 4.5,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q8_0", "size_gb": 8.19,
        "hf_gguf_repo": "unsloth/gemma-4-E4B-it-GGUF",
    },
    "gemma-4-26B-A4B-it": {
        "developer": "Google", "family": "Gemma-4", "params_total_B": 25.2,
        "params_active_B": 3.8, "moe": True, "is_medical": False,
        "reasoning": False, "quant_target": "UD-Q5_K_M", "size_gb": 21.15,
        "hf_gguf_repo": "unsloth/gemma-4-26B-A4B-it-GGUF",
    },
    "Ministral-3-14B-Instruct-2512": {
        "developer": "Mistral AI", "family": "Ministral-3", "params_total_B": 14.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q6_K", "size_gb": 11.09,
        "hf_gguf_repo": "unsloth/Ministral-3-14B-Instruct-2512-GGUF",
    },
    "gemma-4-E2B-it": {
        "developer": "Google", "family": "Gemma-4", "params_total_B": 5.1,
        "params_effective_B": 2.3,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "BF16", "size_gb": 9.31,
        "hf_gguf_repo": "ggml-org/gemma-4-E2B-it-GGUF",
        "checkpoint_file": "gemma-4-E2B-it-BF16.gguf",
        "checkpoint_sha256": "246c5fb19e64b941b531e86639f070bc67b00f20e9ec8556a6c02653321bc65d",
    },
    "gemma-3-27b-it": {
        "developer": "Google", "family": "Gemma-3", "params_total_B": 27.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q5_K_M", "size_gb": 19.27,
        "hf_gguf_repo": "unsloth/gemma-3-27b-it-GGUF",
    },
    "phi-4": {
        "developer": "Microsoft", "family": "Phi-4", "params_total_B": 14.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q6_K", "size_gb": 12.03,
        "hf_gguf_repo": "unsloth/phi-4-GGUF",
    },
    "medgemma-27b-text-it": {
        "developer": "Google", "family": "MedGemma", "params_total_B": 27.0,
        "params_active_B": None, "moe": False, "is_medical": True,
        "reasoning": False, "quant_target": "Q5_K_M", "size_gb": 19.27,
        "hf_gguf_repo": "unsloth/medgemma-27b-text-it-GGUF",
    },
    "granite-4.1-30b": {
        "developer": "IBM", "family": "Granite-4.1", "params_total_B": 30.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q5_K_M", "size_gb": 20.49,
        "hf_gguf_repo": "ibm-granite/granite-4.1-30b-GGUF",
    },
    "granite-4.1-8b": {
        "developer": "IBM", "family": "Granite-4.1", "params_total_B": 8.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q6_K", "size_gb": 7.22,
        "hf_gguf_repo": "ibm-granite/granite-4.1-8b-GGUF",
    },
    "gemma-3-4b-it": {
        "developer": "Google", "family": "Gemma-3", "params_total_B": 4.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q8_0", "size_gb": 4.13,
        "hf_gguf_repo": "unsloth/gemma-3-4b-it-GGUF",
    },
    "Meta-Llama-3.1-8B-Instruct": {
        "developer": "Meta", "family": "Llama-3.1", "params_total_B": 8.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q6_K", "size_gb": 6.60,
        "hf_gguf_repo": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
    },
    "Olmo-3-7B-Instruct": {
        "developer": "Ai2", "family": "OLMo-3", "params_total_B": 7.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q8_0", "size_gb": 7.76,
        "hf_gguf_repo": "lmstudio-community/Olmo-3-7B-Instruct-GGUF",
    },
    "gemma-3-1b-it": {
        "developer": "Google", "family": "Gemma-3", "params_total_B": 1.0,
        "params_active_B": None, "moe": False, "is_medical": False,
        "reasoning": False, "quant_target": "Q8_0", "size_gb": 1.07,
        "hf_gguf_repo": "ggml-org/gemma-3-1b-it-GGUF",
    },
    "medgemma-1.5-4b-it": {
        "developer": "Google", "family": "MedGemma-1.5", "params_total_B": 4.0,
        "params_active_B": None, "moe": False, "is_medical": True,
        "reasoning": False, "quant_target": "Q8_0", "size_gb": 4.13,
        "hf_gguf_repo": "unsloth/medgemma-1.5-4b-it-GGUF",
    },
}


def main() -> int:
    out_dir = Path(__file__).resolve().parent
    for model_id, config in MODELS.items():
        text = json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (out_dir / f"{model_id}.json").write_text(text, encoding="utf-8")
    print(f"wrote {len(MODELS)} model_config.json files into {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
