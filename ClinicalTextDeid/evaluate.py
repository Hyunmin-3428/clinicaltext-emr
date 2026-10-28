"""Synthetic-only local benchmark; never save source/capsules/keys in results."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import re

from .api import transform, restore, public_payload
from .detectors import DIRECT, detect, detect_all

ROOT = Path(__file__).resolve().parent
QUASI = {"ORGANIZATION", "EVENT"}
CONTEXT = {"project": "synthetic-evaluation", "document": "fixture", "release": "v1", "purpose": "research"}


def load_fixtures():
    cases = json.loads((ROOT / "fixtures.json").read_text(encoding="utf-8"))
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        gold_spans(case)
    return cases


def gold_spans(case):
    """Compile manually annotated literal spans. Gold is never passed to a detector."""
    spans = []
    for value, kind in case["phi"]:
        starts = [m.start() for m in re.finditer(re.escape(value), case["text"])]
        if len(starts) != 1:
            raise ValueError(f"Fixture {case['id']} needs a unique literal gold span")
        spans.append({"start": starts[0], "end": starts[0] + len(value), "type": kind})
    return spans


def upstream(text):
    """Unchanged upstream call; no adapter normalization; upstream defaults intact."""
    return [{"start": d.start, "end": d.end, "type": d.label}
            for d in detect_all(text, include=DIRECT)]


def metrics(counts):
    tp, fp, fn = (counts.get(k, 0) for k in ["tp", "fp", "fn"])
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def group_spans(spans, group):
    if group == "all":
        return spans
    return [s for s in spans if (s["type"] in QUASI) == (group == "quasi")]


def char_counts(predicted, gold):
    def points(spans):
        return {i for s in spans for i in range(s["start"], s["end"])}
    p, g = points(predicted), points(gold)
    return Counter(tp=len(p & g), fp=len(p - g), fn=len(g - p))


def strict_counts(predicted, gold, *, typed=True):
    def spans(values):
        return {(s["start"], s["end"], s["type"]) if typed else (s["start"], s["end"])
                for s in values}
    p, g = spans(predicted), spans(gold)
    return Counter(tp=len(p & g), fp=len(p - g), fn=len(g - p))


def finite_semantic_checks(case, result):
    """Fixture obligations, independent from API's own regex semantic status."""
    output = result["public"]["text"]
    checks = [("sentinel", phrase in output) for phrase in case.get("sentinels", [])]
    if case.get("no_inferred_gender"):
        checks.append(("no_gender_inference", re.search(r"\b(?:male|female)\b|남성|여성", output) is None))
    if "family" in case:
        expected = case["family"]
        checks.append(("family_facts", result["assessment"]["semantic_checks"]["family_rule_facts"] == expected))
        for fact in expected:
            relation, age = fact["relation"], fact["age"]
            korean = relation in {"아버지", "어머니", "형제", "자매"}
            detail = (f"{relation}; {age}세 " + ("사망" if fact["fatal"] else "발병 없음" if fact["negated"] else "발병")) if korean else (
                f"{relation}; " + ("died at " if fact["fatal"] else "no MI at " if fact["negated"] else "MI at ") + str(age))
            qualifier = ("음성" if fact["negated"] else "양성") if korean else ("negative" if fact["negated"] else "positive")
            checks.append(("family_output_subject_age_outcome", detail in output))
            checks.append(("family_output_negation", qualifier in output))
            premature_phrase = "조기 " if korean else "premature "
            checks.append(("family_output_prematurity", (premature_phrase in output) == fact["premature_rule"]))
    if case.get("unsupported_family"):
        checks.append(("unsupported_family_no_rewrite", not result["assessment"]["semantic_checks"]["family_rule_facts"]))
    return checks


def run(detectors=("upstream", "rules", "hybrid")):
    fixtures = load_fixtures()
    summary = {"schema": "clinical-text-deid-synthetic-evaluation/v1", "fixture_count": len(fixtures),
        "scope": "finite handcrafted synthetic fixtures; not general clinical or privacy performance",
        "unit": "Unicode code points, not UTF-8 bytes or paper tokens",
        "gold_hints_used": False, "external_model_api_calls": False,
        "upstream_baseline": "unchanged detect_all(original_text, include=DIRECT); upstream default normalize=True retained; no adapter normalization or supplements",
        "quasi_definition": "ORGANIZATION and EVENT only; age/date policy separately tested; not complete quasi-identifier coverage",
        "strict_spans": "exact start/end and exact type; MIXED_PHI is not a matching gold type",
        "character_metrics": "union of positions; all is type-agnostic, category subsets use predicted/gold type",
        "data_storage": "results omit source/public text, key, ciphertext, capsule, PHI literals",
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                          for name in ["api.py", "detectors.py", "generalize.py", "clinical_guards.py", "cli.py", "fixtures.json", "evaluate.py"]},
        "detectors": {}}
    for detector in detectors:
        totals = {g: {m: Counter() for m in ["characters", "strict_typed", "strict_boundary"]}
                  for g in ["all", "direct", "quasi"]}
        strata = {}
        per_case = []
        for case in fixtures:
            gold = gold_spans(case)
            predicted = upstream(case["text"]) if detector == "upstream" else detect(case["text"], detector=detector)
            row = {"id": case["id"], "stratum": case["stratum"]}
            for group in totals:
                p, g = group_spans(predicted, group), group_spans(gold, group)
                cm, sm, bm = char_counts(p, g), strict_counts(p, g), strict_counts(p, g, typed=False)
                for name, counts in [("characters", cm), ("strict_typed", sm), ("strict_boundary", bm)]:
                    totals[group][name].update(counts)
                row[group] = {"characters": dict(cm), "strict_typed": dict(sm)}
            strata.setdefault(case["stratum"], Counter()).update(row["all"]["characters"])
            per_case.append(row)
        summary["detectors"][detector] = {
            "metrics": {g: {m: metrics(c) for m, c in values.items()} for g, values in totals.items()},
            "by_stratum_character_metrics": {s: metrics(c) for s, c in strata.items()},
            "cases": per_case}
        if detector != "upstream":
            key = os.urandom(32)
            restoration, semantics = Counter(), Counter()
            semantic_failures = []
            for case in fixtures:
                context = CONTEXT | {"document": case["id"]}
                for mode in ["direct", "context_rules"]:
                    request = {"text": case["text"], "context": context, "detector": detector,
                               "policy": {"mode": mode}}
                    result = transform(request, key=key)
                    restoration["total"] += 1
                    restoration["byte_exact"] += restore(result, key=key, context=context) == case["text"].encode("utf-8")
                    # No capsules or raw text persist after this transient loop.
                    assert set(public_payload(result)) == {"schema_version", "text", "validation_status"}
                    if mode == "context_rules":
                        checks = finite_semantic_checks(case, result)
                        semantics["obligations"] += len(checks)
                        semantics["passed"] += sum(ok for _, ok in checks)
                        failed = [name for name, ok in checks if not ok]
                        if failed:
                            semantic_failures.append({"id": case["id"], "failed_obligations": failed})
            summary["detectors"][detector]["restoration"] = dict(restoration)
            summary["detectors"][detector]["finite_clinical_obligations"] = dict(semantics)
            summary["detectors"][detector]["semantic_failures"] = semantic_failures
    packages = {}
    for name in ["cryptography", "presidio-analyzer", "spacy", "en-core-web-sm"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not_installed"
    summary["package_versions"] = packages
    return summary


def percent(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def report(summary):
    lines = ["# 합성 평가 결과", "", f"수동 작성 합성 문장 {summary['fixture_count']}개. 실제 환자 자료 및 외부 모델 API 사용 없음.",
        "정답 구간은 평가에만 쓰며 탐지기에는 전달하지 않았습니다. 문자 단위는 Unicode code point입니다.",
        "이 수치는 일반 임상 자료의 성능, 반출 승인, 재식별 위험 추정이 아닙니다.", "",
        "## 직접식별자와 문맥 준식별자", "",
        "ORGANIZATION/EVENT를 제한된 준식별자 범주로 따로 측정했습니다. 나이·날짜 등 모든 준식별자를 포함하는 점수가 아닙니다.", "",
        "| 탐지기 | 범주 | 문자 TP / FP / FN | Precision | Recall | F1 | 정확 유형·구간 TP / FP / FN |", "|---|---|---|---|---|---|---|"]
    for name, data in summary["detectors"].items():
        for group, vals in data["metrics"].items():
            c, s = vals["characters"], vals["strict_typed"]
            lines.append(f"| {name} | {group} | {c['tp']} / {c['fp']} / {c['fn']} | {percent(c['precision'])} | {percent(c['recall'])} | {percent(c['f1'])} | {s['tp']} / {s['fp']} / {s['fn']} |")
    lines += ["", "## 복원과 임상 의미 검사", "",
        "byte-exact 복원은 UTF-8 바이트 비교입니다. 임상 검사는 합성 정답의 유한한 sentinel/가족 주체·부정·나이·사망 경과·성별 추론 금지 의무를 별도로 확인합니다.",
        "일반 자유서술의 의학적 의미 동등성을 검증한 것은 아닙니다.", ""]
    for name, data in summary["detectors"].items():
        if "restoration" in data:
            r, s = data["restoration"], data["finite_clinical_obligations"]
            lines.append(f"- {name}: UTF-8 복원 {r['byte_exact']}/{r['total']}; 임상 의무 {s['passed']}/{s['obligations']}; 실패 {len(data['semantic_failures'])}문장.")
    lines += ["", "## 실제 발견한 실패", "", "아래에는 합성 case ID와 오류 수만 기록하며 원문·PHI 문자열·키·캡슐은 기록하지 않습니다.", ""]
    for name, data in summary["detectors"].items():
        failed = [r for r in data["cases"] if r["all"]["characters"]["fn"] or r["all"]["characters"]["fp"]]
        lines.append(f"### {name}: 탐지 오차가 있는 문장 {len(failed)}개")
        lines.append("")
        lines += [f"- `{r['id']}`: FP {r['all']['characters']['fp']}, FN {r['all']['characters']['fn']}" for r in failed]
        lines += [f"- 임상 의무 실패 `{r['id']}`: {', '.join(r['failed_obligations'])}" for r in data.get("semantic_failures", [])]
        lines.append("")
    lines += ["## 해석 범위", "", "upstream은 ko-pii 원문 호출의 자체 기본 normalize=True를 유지하며 adapter 정규화/보충 규칙은 사용하지 않습니다. rules는 원문 위치 역매핑과 임상 문맥 정규식을 더하고, hybrid는 공개 Presidio/spaCy 로컬 영어 NER를 더합니다.",
        "알 수 없는 이름·서술형 주소 등의 누락과 구간 과탐지가 남습니다. 질병명/장치명 임상 오탐에 넣은 좁은 보호 규칙도 모든 표현을 지원하지 않습니다. 모델 사용은 LLM 일반화나 자유서술 안전성 검증을 의미하지 않습니다.",
        "합성 사례는 탐지 구현 개발에 사용된 작은 convenience set으로 독립 검증 세트가 아닙니다. 분모·오류 수를 숨기지 않고 실패를 다음 검토 단계로 전달해야 합니다.",
        "SNUBH 논문의 토큰 F1 등 연구 수치와 이 문자/구간 점수를 비교하지 않습니다."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detectors", nargs="+", choices=["upstream", "rules", "hybrid"], default=["upstream", "rules", "hybrid"])
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluation")
    args = parser.parse_args()
    result = run(args.detectors)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "report.md").write_text(report(result), encoding="utf-8")
    print(json.dumps({"fixture_count": result["fixture_count"], "detectors": list(result["detectors"]),
                      "report": str(args.output_dir / "report.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
