"""Opt-in live-model evaluation. ONLY author-created synthetic notes are sent.

Run from the workspace after Ollama and qwen3:4b-instruct are installed.
The normal tests use simulations; this script always calls a real model server.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
from pathlib import Path
import time
from urllib.request import HTTPCookieProcessor, Request, build_opener

from ClinicalCDSGateway.gateway.engine import verify_external
from ClinicalCDSGateway.gateway.fixtures import FIXTURES
from ClinicalTextWeb.llm_client import ModelConfig, StructuredClient
from ClinicalTextWeb.llm_pipeline import VERIFY_INSTRUCTIONS, VERIFY_SCHEMA
from ClinicalTextWeb.llm_service import SURGERY, NEONATAL, SURROGATE, SURROGATE_PURPOSE


CASES = {
    "surrogate": {"purpose": SURROGATE_PURPOSE, "text": SURROGATE,
                  "checks": ["treatment", "time", "negation", "family"], "required_phrases": [],
                  "expected": ["2023년 3월 10일", "2018년", "충수절제술을 시행받았다", "수술 3일 후에도 발열은 없었고", "F/U CT에서 특이 소견은 없었다", "무릎 통증", "부친이 52세에 심근경색으로 사망하였다"],
                  "private": ["김민수", "47세", "연세대학교", "서울마라톤"]},
    "bilingual": {"purpose": "심혈관 연구를 위한 혈압과 질환 부정 표현 보존", "text": FIXTURES["connected-hybrid"]["source"],
                  "checks": ["measurements", "negation"], "required_phrases": [], "expected": ["No evidence of MI", "BP 120/80 mmHg"], "private": ["김민준", "John Smith"]},
    "surgery": {"purpose": "수술 후 감염 위험인자 연구", "text": SURGERY,
                "checks": ["treatment", "time", "negation"], "required_phrases": [], "expected": ["3월 10일", "충수절제술 시행", "수술 3일 후에도 발열 없음", "수술 부위 감염 소견 없음"], "private": ["김민수"]},
    "neonatal": {"purpose": "신생아 뇌손상과 호흡부전 연구", "text": NEONATAL,
                 "checks": ["negation", "time"], "required_phrases": [], "expected": ["Hypoxic ischaemic encephalopathy of newborn", "Respiratory failure of newborn", "Metabolic acidosis", "No evidence of neonatal sepsis"], "private": ["김민준"]},
}


def run(url, model, output_dir, cases, damage_test=False, model_url="http://127.0.0.1:11435"):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    def call(path, payload=None):
        if payload is None:
            req = Request(url + path)
        else:
            req = Request(url + path, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
                          headers={"Content-Type": "application/json", "X-CSRF-Token": csrf})
        with opener.open(req, timeout=1500) as response:
            return json.load(response)
    bootstrap = call("/api/bootstrap")
    csrf = bootstrap["csrf"]
    config = {"provider": "ollama", "base_url": model_url, "model": model, "verifier_model": model, "api_key": ""}
    call("/api/model/configure", config)
    print("LIVE connection probe", flush=True)
    connection_start = time.perf_counter()
    connection = call("/api/model/probe", {})
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "model": model,
              "scope": "Real local LLM; author-created synthetic notes ONLY; no training", "connection": connection,
              "probe_seconds": round(time.perf_counter() - connection_start, 2), "cases": {}}
    def save():
        (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    for name in cases:
        case = CASES[name]
        start = time.perf_counter()
        print("LIVE analyze " + name, flush=True)
        outcome = {"purpose": case["purpose"], "source": case["text"]}
        try:
            view = call("/api/analyze", {key: case[key] for key in ["purpose", "text", "checks", "required_phrases"]} | {"engine": "llm", "synthetic_consent": False})
            outcome.update({"analysis_seconds": round(time.perf_counter() - start, 2), "view": view,
                            "expected_clinical_phrases_preserved": all(value in view["output"] for value in case["expected"]),
                            "direct_identifiers_removed": all(value not in view["output"] for value in case["private"])})
            if view["can_approve"]:
                approval = {"analysis_id": view["analysis_id"], "review_digest": view["review_digest"], "confirmed": True}
                call("/api/approve", approval)
                exported = call("/api/export", approval)
                raw = base64.b64decode(exported["artifact_b64"])
                envelope = json.loads(raw)
                key = base64.b64decode(exported["verification_key_b64"])
                payload = verify_external(envelope, key)
                outcome["signed_export_verified"] = payload["text"] == view["output"]
                outcome["no_private_export_fields"] = all(key not in payload for key in ["source", "api_key", "mapping", "capsule", "evidence"])
                (output_dir / (name + ".public.json")).write_bytes(raw)
                (output_dir / (name + ".verification.key")).write_bytes(key)
            else:
                outcome["signed_export_verified"] = False
            outcome["passed"] = bool(outcome["expected_clinical_phrases_preserved"] and outcome["direct_identifiers_removed"] and outcome["signed_export_verified"] and outcome.get("no_private_export_fields"))
        except Exception as error:
            outcome["passed"] = False
            outcome["error_type"] = type(error).__name__
            if hasattr(error, "read"):
                try:
                    outcome["api_error"] = json.loads(error.read(3000))
                except Exception:
                    pass
            outcome["analysis_seconds"] = round(time.perf_counter() - start, 2)
        report["cases"][name] = outcome
        save()
        print(json.dumps({"case": name, "passed": outcome["passed"], "seconds": outcome["analysis_seconds"], "state": outcome.get("view", {}).get("state"), "error": outcome.get("api_error")}, ensure_ascii=True), flush=True)
    if damage_test:
        print("LIVE deliberately damaged neonatal diagnosis audit", flush=True)
        client = StructuredClient(ModelConfig.parse(config))
        original = NEONATAL
        damaged = original.replace("김민준", "<PERSON_1>").replace("Respiratory", "<PERSON_2>").replace("Metabolic", "<PERSON_3>")
        conditions = [{"id": "respiratory", "relation": "신생아 호흡부전 진단 유지", "source_quote": "Respiratory failure of newborn"},
                      {"id": "metabolic", "relation": "대사성 산증 진단 유지", "source_quote": "Metabolic acidosis"}]
        started = time.perf_counter()
        try:
            audit = client.complete("verify", VERIFY_INSTRUCTIONS, {"purpose": "신생아 호흡부전 및 산증 연구", "original": original, "output": damaged,
                        "conditions": conditions, "protected_diagnoses": [item["source_quote"] for item in conditions], "required_phrases": []}, VERIFY_SCHEMA)
            report["damaged_diagnosis_test"] = {"audit": audit, "seconds": round(time.perf_counter() - started, 2),
                "passed": all(any(check["id"] == item["id"] and check["status"] in {"failed", "unknown"} for check in audit["checks"]) for item in conditions)}
        except Exception as error:
            report["damaged_diagnosis_test"] = {"passed": False, "error": type(error).__name__}
        save()
    report["successful"] = all(case["passed"] for case in report["cases"].values()) and (not damage_test or report["damaged_diagnosis_test"]["passed"])
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--model-url", default="http://127.0.0.1:11435", help="GPU server through SSH; local CPU inference is not the default")
    parser.add_argument("--out-dir", default="ClinicalTextWeb/.runtime/live-validation")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--damage-test", action="store_true")
    args = parser.parse_args()
    result = run(args.url, args.model, args.out_dir, args.cases, args.damage_test, args.model_url)
    raise SystemExit(0 if result["successful"] else 1)


if __name__ == "__main__":
    main()
