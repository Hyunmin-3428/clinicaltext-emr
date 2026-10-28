"""LLM web workflow; only server-authored synthetic source texts may be released."""
from __future__ import annotations

import base64
import copy
from pathlib import Path
import time
import uuid

from ClinicalCDSGateway.gateway.common import Denied, canonical, digest, plain_text, strict_json
from ClinicalCDSGateway.gateway.detector import Candidate, residual_check
from ClinicalCDSGateway.gateway.engine import Gateway, verify_external
from ClinicalCDSGateway.gateway.fixtures import FIXTURES, request
from ClinicalTextDeid.detectors import detect
from .llm_client import ModelConfig, StructuredClient
from .llm_pipeline import PurposePipeline, audit_evidence
from .risk_candidates import detect_for_research
from .temporal_policy import permitted_calendar_occurrences, preserved_date_candidate, POLICY_VERSION
from .synthetic_notes import neonatal_examples

SURGERY = "환자 김민수. 3월 10일 충수절제술 시행. 수술 3일 후에도 발열 없음. 수술 부위 감염 소견 없음."
NEONATAL = "성명: 김민준.\n■ Dx\n#Hypoxic ischaemic encephalopathy of newborn\n#Respiratory failure of newborn\n#Metabolic acidosis\n■ PI\n생후 1일. No evidence of neonatal sepsis."
SURROGATE = "김민수(47세, 연세대학교 교수)는 2023년 3월 10일 본원에서 충수절제술을 시행받았다. 수술 3일 후에도 발열은 없었고, F/U CT에서 특이 소견은 없었다. 과거력상 2018년 서울마라톤 완주 후 무릎 통증이 있었으며, 부친이 52세에 심근경색으로 사망하였다."
SURROGATE_PURPOSE = "수술 후 감염 및 임상 경과 연구. 충수절제술의 실제 수술 날짜(연·월·일), 수술 후 경과일, 발열·F/U CT의 부정 표현, 과거력의 연도와 장거리 운동 후 무릎 통증, 부친의 심근경색 사망 가족관계와 52세라는 나이는 보존한다. 환자의 정확한 나이와 이름, 대학명·직함, 구체적인 행사명은 일반화한다."
AUTHORED_SOURCES = ({digest(item["source"]) for item in FIXTURES.values()}
                    | {digest(SURGERY), digest(NEONATAL), digest(SURROGATE)}
                    | {digest(item["text"]) for item in neonatal_examples()})


class EvidenceRegistry:
    def __init__(self):
        self.entries = {}

    def is_registered(self, source):
        return digest(source) in AUTHORED_SOURCES

    def validate(self, source_request, output):
        entry = self.entries.get(source_request["document"])
        if entry is None or entry["bundle"].get("policy_version") != POLICY_VERSION or digest(source_request["text"]) != entry["source_hash"] or digest(output) != entry["output_hash"]:
            return ("LLM_EVIDENCE_BINDING_FAILED",)
        checked = audit_evidence(source_request["text"], output, entry["purpose"], entry["checks"], entry["phrases"], entry["bundle"]["plan"], entry["bundle"]["audit"], applied_edits=entry["bundle"].get("applied_edits", []))
        if checked != entry["validation"] or not checked["eligible"]:
            return ("LLM_CLINICAL_VALIDATION_FAILED",)
        return ()

    def permits_clinical_dates(self, source_request, output):
        return (self.is_registered(source_request["text"]) and not self.validate(source_request, output)
                and permitted_calendar_occurrences(source_request["text"], output))


class PreparedAdapter:
    def __init__(self, registry):
        self.registry = registry
        self.prepared = None

    def transform_bound(self, text, key, context):
        entry = self.prepared
        if entry is None or entry["source_hash"] != digest(text):
            raise Denied("LLM_EVIDENCE_BINDING_FAILED")
        return Candidate(entry["output"], (), self.registry.is_registered(text) and entry["validation"]["eligible"],
                         None, "purpose-llm-span-v1", {"schema_version": "clinical-purpose-llm/v1", "evidence": entry["bundle"]})


class LLMWorkflow:
    def __init__(self, root, owner, client_factory=StructuredClient):
        self.owner = owner
        self.configs = {}
        self.client_factory = client_factory
        self.registry = EvidenceRegistry()
        self.adapter = PreparedAdapter(self.registry)
        Gateway.initialize(Path(root) / "llm")
        self.gateway = Gateway(Path(root) / "llm", adapter=self.adapter, synthetic_validator=self.registry)
        self.tokens = {role: (self.gateway.internal / f"{role}.token").read_text(encoding="ascii") for role in ["submitter", "reviewer", "exporter"]}

    def settings(self, identifier):
        config = self.configs.get(identifier)
        return config.public() if config else {"configured": False}

    def configure(self, identifier, data):
        self.owner.session(identifier)
        previous = self.configs.get(identifier)
        if type(data) is dict and not data.get("api_key") and previous and data.get("provider") == previous.provider and data.get("base_url", "").rstrip("/") == previous.base_url:
            data = {**data, "api_key": previous.api_key}
        config = ModelConfig.parse(data)
        with self.owner.lock:
            for entry in self.owner.session(identifier).analyses.values():
                if entry.get("engine") == "llm" and entry["view"]["state"] in {"REVIEW_READY", "APPROVED"}:
                    self.gateway.revoke(entry["view"]["analysis_id"], self.tokens["reviewer"])
                    entry["view"]["state"] = "INVALIDATED"
                    entry["view"]["can_approve"] = False
            self.configs[identifier] = config
        return config.public()

    def probe(self, identifier, data):
        if data != {}:
            raise Denied("WEB_INPUT_INVALID")
        self.owner.session(identifier)
        config = self.configs.get(identifier)
        if config is None:
            raise Denied("LLM_NOT_CONFIGURED")
        return self.client_factory(config).probe()

    def analyze(self, identifier, data):
        config = self.configs.get(identifier)
        self.owner.session(identifier)
        if config is None:
            raise Denied("LLM_NOT_CONFIGURED")
        if config.remote and data.get("synthetic_consent") is not True:
            raise Denied("LLM_REMOTE_CONSENT_REQUIRED")
        source, purpose, checks, phrases = (data[key] for key in ["text", "purpose", "checks", "required_phrases"])
        provider=getattr(self,'prepared_provider',None)
        prepared=provider(source,purpose,checks,phrases,config) if provider else None
        if prepared is None:
            candidates = detect_for_research(source)
            client = self.client_factory(config)
            prepared = PurposePipeline(client).run(source, purpose, checks, phrases, candidates)
        output = prepared["output"]
        validation = prepared["validation"]
        raw_residual = detect(output, detector="hybrid")
        resolved_dates = [s for s in raw_residual if preserved_date_candidate(source,output,s)]
        residual = [s for s in raw_residual if s not in resolved_dates]
        with self.owner.lock:
            if self.configs.get(identifier) is not config:
                raise Denied("WEB_REVIEW_CHANGED")
            session = self.owner.session(identifier)
            if len(session.analyses) >= 40:
                raise Denied("WEB_LIMIT")
            document = "llm-" + uuid.uuid4().hex
            evidence = {"source_hash": digest(source), "output_hash": digest(output), "output": output,
                        "purpose": purpose, "checks": checks, "phrases": phrases,
                        "bundle": copy.deepcopy(prepared["bundle"]), "validation": copy.deepcopy(validation)}
            self.registry.entries[document] = evidence
            source_request = request(text=source, document=document)
            independent = list(self.gateway._residual_validation(source_request, output))
            self.adapter.prepared = evidence
            try:
                submitted = self.gateway.submit(source_request, self.tokens["submitter"])
            finally:
                self.adapter.prepared = None
            reasons = []
            registered = self.registry.is_registered(source)
            if not registered:
                reasons.append("LLM으로 처리·검증한 결과입니다. 서버에 등록된 합성 원문이 아니므로 실제 반출은 기관 검토 전까지 보류합니다.")
            if residual or independent or validation["residual_phi_count"]:
                reasons.append("잔여 식별정보 후보에 대한 추가 검토가 필요합니다.")
            if not validation["eligible"]:
                reasons.append("임상관계 손상 또는 근거 불확실성이 남아 있습니다.")
            if submitted["state"] != "REVIEW" or submitted["codes"] != ["HUMAN_APPROVAL_REQUIRED"]:
                reasons.append("반출 게이트웨이의 검증 조건을 충족하지 못했습니다.")
            binding = digest(canonical({"purpose": purpose, "source": digest(source), "output": digest(output),
                                        "checks": checks, "phrases": phrases, "evidence": prepared["bundle"], "config": config.public()}))
            output_spans, source_cursor, output_cursor = [], 0, 0
            for edit in prepared["edits"]:
                output_cursor += edit["start"] - source_cursor
                output_spans.append({"start": output_cursor, "end": output_cursor + len(edit["replacement"]), "type": edit["type"]})
                output_cursor += len(edit["replacement"])
                source_cursor = edit["end"]
            view = {"analysis_id": submitted["release"], "review_digest": binding, "purpose": purpose,
                    "source": source, "output": output, "validation": validation, "phi_count": len(prepared["edits"]),
                    "source_spans": [{key: edit[key] for key in ["start", "end", "type"]} for edit in prepared["edits"]],
                    "output_spans": output_spans,
                    "privacy": {"residual_count": len(residual) + validation["residual_phi_count"], "independent_codes": independent,
                                "resolved_clinical_date_candidates":len(resolved_dates),
                                "status": "passed" if not reasons else "review", "scope": "기존 탐지기와 별도 LLM 호출로 잔여 식별정보를 점검했습니다."},
                    "registered_example": registered, "can_approve": not reasons, "state": "HELD" if reasons else "REVIEW_READY", "reasons": reasons,
                    "llm": {"status": "cached" if prepared.get('cached') else "executed", "cached":bool(prepared.get('cached')), "cache_created_at":prepared.get('cache_created_at'),"original_calls":prepared.get('original_calls'), "model": config.model, "verifier_model": config.verifier_model,
                            "provider": config.provider, "calls": prepared["calls"], "attempts": prepared["attempts"], "metrics": prepared["metrics"]},
                    "events": [{"event": "LLM 목적별 조건·가명처리·별도 검증 완료", "time": time.time()}]}
            session.analyses[submitted["release"]] = {"engine": "llm", "view": view, "digest": binding, "input": copy.deepcopy(data),
                   "evidence": evidence, "config": config.public(), "artifact": None, "text_hash": digest(output)}
            return copy.deepcopy(view)

    def review(self, identifier, data):
        from ClinicalCDSGateway.gateway.common import exact_schema
        import hmac
        exact_schema(data, {"analysis_id", "review_digest", "confirmed"})
        entry = self.owner._entry(identifier, data["analysis_id"])
        if data["confirmed"] is not True:
            raise Denied("WEB_CONFIRM_REQUIRED")
        if type(data["review_digest"]) is not str or not hmac.compare_digest(data["review_digest"], entry["digest"]):
            raise Denied("WEB_REVIEW_CHANGED")
        if entry["view"]["state"] not in {"REVIEW_READY", "APPROVED"} or not entry["view"]["can_approve"]:
            raise Denied("WEB_HELD")
        inputs, view, evidence = entry["input"], entry["view"], entry["evidence"]
        binding = digest(canonical({"purpose": inputs["purpose"], "source": digest(inputs["text"]), "output": digest(view["output"]),
                                    "checks": inputs["checks"], "phrases": inputs["required_phrases"], "evidence": evidence["bundle"], "config": entry["config"]}))
        if binding != entry["digest"] or self.settings(identifier) != entry["config"]:
            raise Denied("WEB_REVIEW_CHANGED")
        return entry

    def approve(self, identifier, data):
        with self.owner.lock:
            entry = self.review(identifier, data)
            self.gateway.approve(data["analysis_id"], self.tokens["reviewer"], entry["text_hash"])
            entry["view"]["state"] = "APPROVED"
            entry["view"]["events"].append({"event": "LLM 검증 결과 검토·승인", "time": time.time()})
            return copy.deepcopy(entry["view"])

    def export(self, identifier, data):
        with self.owner.lock:
            entry = self.review(identifier, data)
            result = self.gateway.export(data["analysis_id"], self.tokens["exporter"])
            raw = Path(result["file"]).read_bytes()
            verify_external(strict_json(raw), (self.gateway.external / "verification.key").read_bytes())
            entry["view"]["state"] = "EXPORTED"
            entry["view"]["can_approve"] = False
            entry["view"]["events"].append({"event": "승인된 LLM 처리 결과 반출", "time": time.time()})
            return {"state": "EXPORTED", "filename": "clinical-llm-" + data["analysis_id"][:8] + ".json",
                    "artifact_b64": base64.b64encode(raw).decode(), "verification_key_b64": base64.b64encode((self.gateway.external / "verification.key").read_bytes()).decode(),
                    "view": copy.deepcopy(entry["view"])}
