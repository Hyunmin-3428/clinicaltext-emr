"""A session-scoped web facade. Existing gateway release restrictions stay intact."""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field
import hmac
import secrets
import threading
import time
import uuid

from ClinicalCDSGateway.gateway.common import Denied, canonical, digest, exact_schema, plain_text, strict_json
from ClinicalCDSGateway.gateway.deid_adapter import ClinicalTextDeidAdapter
from ClinicalCDSGateway.gateway.detector import residual_check
from ClinicalCDSGateway.gateway.engine import Gateway, verify_external
from ClinicalCDSGateway.gateway.fixtures import FIXTURES, find_fixture, request
from .clinical import CHECKS, verify_relations

MESSAGES = {
    "LLM_NOT_CONFIGURED": "LLM 연결이 필요합니다. 모델 연결 설정에서 사용할 모델을 지정해 주세요.",
    "LLM_CONTEXT_LIMIT": "현재 로컬 모델의 문맥 한도에서 원문 전체를 보장할 수 없습니다. 기록을 짧은 단위로 나눠 주세요. 원문을 자동으로 잘라 처리하지 않았습니다.",
    "LLM_CONFIG_INVALID": "모델 종류·서버 주소·모델명·인증 정보를 확인해 주세요. 외부 서버는 HTTPS 주소를 사용해야 합니다.",
    "LLM_REMOTE_CONSENT_REQUIRED": "외부 모델로 전송하기 전에 합성 기록과 전송 목적지를 확인해 주세요.",
    "LLM_UNAVAILABLE": "모델 서버에 연결하지 못했거나 응답 시간이 초과되었습니다. 규칙 처리로 대체하지 않았습니다.",
    "LLM_AUTH_FAILED": "모델 API 인증에 실패했습니다. 연결 설정의 API 키를 확인해 주세요.",
    "LLM_RATE_LIMIT": "모델 API 사용 한도에 도달했습니다. 모델 제공자의 사용량을 확인해 주세요.",
    "LLM_API_FAILED": "모델 API 요청에 실패했습니다. 모델명과 구조화 응답 지원 여부를 확인해 주세요.",
    "LLM_RESPONSE_INVALID": "모델 응답 형식 또는 근거가 올바르지 않아 처리를 보류했습니다.",
    "LLM_RESPONSE_INCOMPLETE": "모델 응답이 완성되지 않아 처리를 보류했습니다.",
    "LLM_EVIDENCE_INVALID": "모델이 제시한 임상관계의 원문 근거를 확인하지 못했습니다.",
    "LLM_CLINICAL_EDIT_DENIED": "모델 수정안이 진단명 등 보호할 임상정보를 침범하여 처리를 보류했습니다.",
    "LLM_EDIT_INVALID": "모델의 수정 구간을 원문에 정확히 적용할 수 없어 보류했습니다.",
    "WEB_INPUT_INVALID": "연구 목적, 기록, 보존 항목을 확인해 주세요.",
    "WEB_SESSION_EXPIRED": "시연 세션이 만료되었습니다. 화면을 새로고침해 주세요.",
    "WEB_NOT_FOUND": "이 세션에서 확인할 수 없는 결과입니다. 다시 검사해 주세요.",
    "WEB_HELD": "미충족 조건이 있어 반출을 승인할 수 없습니다. 검사 결과를 확인해 주세요.",
    "WEB_REVIEW_CHANGED": "검토 대상이 달라졌습니다. 다시 검사하고 승인해 주세요.",
    "WEB_CONFIRM_REQUIRED": "검토 확인란을 선택해 주세요.",
    "WEB_LIMIT": "시연 처리 한도에 도달했습니다. 프로그램을 다시 시작해 주세요.",
    "NOT_REVIEWABLE": "이미 처리되었거나 승인할 수 없는 결과입니다.",
    "NOT_APPROVED_OR_CONSUMED": "승인이 필요하거나 이미 반출한 결과입니다.",
    "REAL_TEXT_RELEASE_DISABLED": "등록된 합성 예제만 반출할 수 있습니다.",
    "REVIEW_EXPIRED": "검토 시간이 만료되었습니다. 다시 검사해 주세요.",
    "APPROVAL_EXPIRED": "승인 시간이 만료되었습니다. 다시 검사하고 승인해 주세요.",
    "TEXT_LIMIT": "기록은 비어 있지 않은 32KB 이내의 텍스트여야 합니다.",
    "NONCANONICAL_TEXT": "호환 문자 등이 포함되어 있습니다. 일반 텍스트로 입력해 주세요.",
    "UNSUPPORTED_ENCODING": "지원하지 않는 숨김 문자 또는 제어 문자가 포함되어 있습니다.",
    "UNSUPPORTED_CONTENT": "의료기록의 일반 텍스트만 입력해 주세요.",
}


class CapturedAdapter(ClinicalTextDeidAdapter):
    def __init__(self, detector="hybrid"):
        super().__init__(detector)
        self.captured = {}

    def transform_bound(self, text, key, context):
        candidate = super().transform_bound(text, key, context)
        self.captured[context["release"]] = candidate
        return candidate


@dataclass
class Session:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created: float = field(default_factory=time.time)
    analyses: dict = field(default_factory=dict)


def examples():
    from .llm_service import SURGERY, NEONATAL, SURROGATE, SURROGATE_PURPOSE
    return [
        {"id": "surrogate", "label": "모식도 예시 · 자연스러운 대체 기록", "purpose": SURROGATE_PURPOSE,
         "checks": ["treatment", "time", "negation", "family"], "text": SURROGATE, "note": "가상의 인물로 작성한 합성 예제입니다. 이름·기관·행사는 일반화하고, 실제 수술 날짜·3일 경과·가족관계·52세 사망 정보를 보존합니다."},
        {"id": "bilingual", "label": "한영 혼용 · 승인 시연", "purpose": "심혈관 연구를 위한 혈압과 질환 부정 표현 보존",
         "checks": ["measurements", "negation"], "text": FIXTURES["connected-hybrid"]["source"], "note": "등록된 합성 예제입니다. 실제 탐지기 연결부터 승인·반출까지 시연합니다."},
        {"id": "surgery", "label": "수술 기록 · 목적별 처리", "purpose": "수술 후 감염 위험인자 연구",
         "checks": ["treatment", "time", "negation"], "text": SURGERY, "note": "서버에 등록한 합성 수술 기록입니다. LLM 모드에서는 별도 검증 통과 후 승인할 수 있습니다."},
        {"id": "neonatal", "label": "신생아 기록 · 진단명 보존", "purpose": "신생아 뇌손상과 호흡부전 연구",
         "checks": ["negation", "time"], "text": NEONATAL, "note": "가상 환자의 합성 예제입니다. Respiratory failure와 Metabolic acidosis 등 진단명을 보존합니다."},
        {"id": "medication", "label": "투약 서술 · 보존 점검", "purpose": "폐렴 약물 치료 연구. 환자의 정확한 나이 47세, 폐렴 진단, 발열 없음, amoxicillin 500 mg 경구 1일 3회 복용 중이라는 약물·용량·경로·횟수를 필수 보존한다. 이름, 연락처, 병록번호는 가명처리한다.",
         "checks": ["treatment", "measurements", "negation"], "text": FIXTURES["ko-treatment"]["source"], "note": "합성 자유서술 안의 약물 용량과 부정 표현을 점검합니다. 정형 투약 DB는 처리하지 않습니다."},
    ]


class DemoService:
    def __init__(self, root, adapter=None, *, local_model_config=None):
        Gateway.initialize(root)
        self.adapter = adapter or CapturedAdapter("hybrid")
        self.gateway = Gateway(root, adapter=self.adapter)
        self.tokens = {role: (self.gateway.internal / f"{role}.token").read_text(encoding="ascii") for role in ["submitter", "reviewer", "exporter"]}
        self.sessions = {}
        self.lock = threading.RLock()
        self.local_model_config = local_model_config
        from .llm_service import LLMWorkflow
        self.llm = LLMWorkflow(root, self)

    def new_session(self):
        with self.lock:
            now = time.time()
            self.sessions = {key: value for key, value in self.sessions.items() if now - value.created < 7200}
            if len(self.sessions) >= 32:
                raise Denied("WEB_LIMIT")
            identifier = secrets.token_urlsafe(32)
            self.sessions[identifier] = Session()
            if self.local_model_config:
                self.llm.configure(identifier, self.local_model_config)
            return identifier, self.sessions[identifier]

    def session(self, identifier):
        session = self.sessions.get(identifier)
        if session is None or time.time() - session.created > 7200:
            raise Denied("WEB_SESSION_EXPIRED")
        return session

    def bootstrap(self, identifier):
        return {"csrf": self.session(identifier).csrf, "examples": examples(), "checks": CHECKS,
                "scope": "local-synthetic-only", "detector": "ko-pii + Presidio / spaCy", "llm": self.llm.settings(identifier)}

    def _entry(self, identifier, analysis_id):
        if type(analysis_id) is not str:
            raise Denied("WEB_NOT_FOUND")
        entry = self.session(identifier).analyses.get(analysis_id)
        if entry is None:
            raise Denied("WEB_NOT_FOUND")
        return entry

    def analyze(self, identifier, data):
        required = {"purpose", "text", "checks", "required_phrases"}
        if type(data) is not dict or not required <= set(data) or set(data) - required - {"engine", "synthetic_consent"}:
            raise Denied("SCHEMA_DENIED")
        if data.get("engine", "rules") not in {"llm", "rules"} or type(data.get("synthetic_consent", False)) is not bool:
            raise Denied("WEB_INPUT_INVALID")
        purpose, text, checks, phrases = (data[key] for key in ["purpose", "text", "checks", "required_phrases"])
        if (type(purpose) is not str or not purpose.strip() or len(purpose) > 800
                or type(checks) is not list or not checks or len(checks) > len(CHECKS)
                or any(type(item) is not str or item not in CHECKS for item in checks)
                or len(set(checks)) != len(checks)
                or type(phrases) is not list or len(phrases) > 8
                or any(type(item) is not str or not item.strip() or len(item) > 100 for item in phrases)):
            raise Denied("WEB_INPUT_INVALID")
        plain_text(text)
        if data.get("engine") == "llm":
            return self.llm.analyze(identifier, data)
        with self.lock:
            session = self.session(identifier)
            if len(session.analyses) >= 40:
                raise Denied("WEB_LIMIT")
            submitted = self.gateway.submit(request(text=text, document="web-" + uuid.uuid4().hex), self.tokens["submitter"])
            release = submitted["release"]
            review = self.gateway.inspect(release, self.tokens["reviewer"])
            candidate = self.adapter.captured.pop(release, None)
            result = candidate.internal_result if candidate else None
            if result is None or review["candidate"] is None:
                raise Denied("DEID_EXECUTION_FAILED")
            output = review["candidate"]
            validation = verify_relations(text, output, checks, purpose, phrases)
            residual = result["assessment"]["residual_candidates"]
            independent = list(residual_check(text, output))
            fixture_id, fixture = find_fixture(text)
            reasons = []
            if fixture is None:
                reasons.append("기존 규칙 모드의 반출 허용 예제가 아닙니다. LLM 모드로 처리·검증하거나 기관 검토가 필요합니다.")
            if residual or independent:
                reasons.append("잔여 식별정보 후보가 발견되었습니다.")
            if not validation["eligible"]:
                reasons.append("필수 보존 항목이 손상되었거나 원문 근거를 확인하지 못했습니다.")
            if submitted["state"] != "REVIEW" or submitted["codes"] != ["HUMAN_APPROVAL_REQUIRED"]:
                if fixture is not None:
                    reasons.append("기존 게이트웨이의 개인정보·임상 검사 조건을 충족하지 못했습니다.")
            eligible = not reasons
            binding = digest(canonical({"release": release, "purpose": purpose, "checks": checks,
                                        "required_phrases": phrases, "source_sha256": digest(text),
                                        "text_sha256": review["text_sha256"], "validation": validation}))
            view = {"analysis_id": release, "review_digest": binding, "purpose": purpose,
                    "source": text, "output": output, "validation": validation,
                    "phi_count": len(result["audit"]["processed_spans"]),
                    "source_spans": [{"start": span["start"], "end": span["end"], "type": span["type"]} for span in result["audit"]["processed_spans"]],
                    "privacy": {"residual_count": len(residual), "independent_codes": independent,
                                "status": "passed" if eligible else "review", "scope": "탐지된 잔여 후보에 대한 검사이며, 재식별 위험이 없다는 판정은 아닙니다."},
                    "registered_example": fixture_id is not None, "can_approve": eligible,
                    "state": "REVIEW_READY" if eligible else "HELD", "reasons": reasons,
                    "events": [{"event": "가명처리 및 보존 검사 완료", "time": time.time()}]}
            session.analyses[release] = {"view": view, "digest": binding, "text_hash": review["text_sha256"],
                                        "input": copy.deepcopy(data), "artifact": None, "created": time.time()}
            return copy.deepcopy(view)

    def _review(self, identifier, data):
        exact_schema(data, {"analysis_id", "review_digest", "confirmed"})
        entry = self._entry(identifier, data["analysis_id"])
        if data["confirmed"] is not True:
            raise Denied("WEB_CONFIRM_REQUIRED")
        if type(data["review_digest"]) is not str or not hmac.compare_digest(data["review_digest"], entry["digest"]):
            raise Denied("WEB_REVIEW_CHANGED")
        if not entry["view"]["can_approve"] or entry["view"]["state"] not in {"REVIEW_READY", "APPROVED"}:
            raise Denied("WEB_HELD")
        # Re-evaluate the immutable source/settings before both approval and export.
        inputs = entry["input"]
        checked = verify_relations(inputs["text"], entry["view"]["output"], inputs["checks"], inputs["purpose"], inputs["required_phrases"])
        rebound = digest(canonical({"release": data["analysis_id"], "purpose": inputs["purpose"], "checks": inputs["checks"],
                                    "required_phrases": inputs["required_phrases"], "source_sha256": digest(inputs["text"]),
                                    "text_sha256": digest(entry["view"]["output"]), "validation": checked}))
        if rebound != entry["digest"] or checked != entry["view"]["validation"] or not checked["eligible"]:
            raise Denied("WEB_REVIEW_CHANGED")
        return entry

    def approve(self, identifier, data):
        if type(data) is dict and self._entry(identifier, data.get("analysis_id")).get("engine") == "llm":
            return self.llm.approve(identifier, data)
        with self.lock:
            entry = self._review(identifier, data)
            self.gateway.approve(data["analysis_id"], self.tokens["reviewer"], entry["text_hash"])
            entry["view"]["state"] = "APPROVED"
            entry["view"]["events"].append({"event": "검토 결과 승인", "time": time.time()})
            return copy.deepcopy(entry["view"])

    def export(self, identifier, data):
        if type(data) is dict and self._entry(identifier, data.get("analysis_id")).get("engine") == "llm":
            return self.llm.export(identifier, data)
        with self.lock:
            entry = self._review(identifier, data)
            if entry["view"]["state"] != "APPROVED":
                raise Denied("NOT_APPROVED_OR_CONSUMED")
            try:
                exported = self.gateway.export(data["analysis_id"], self.tokens["exporter"])
            except Denied:
                entry["view"]["can_approve"] = False
                entry["view"]["state"] = "HELD"
                entry["view"]["reasons"] = ["승인이 만료되었거나 반출을 완료하지 못했습니다. 다시 검사해 주세요."]
                raise
            from pathlib import Path
            raw = Path(exported["file"]).read_bytes()
            envelope = strict_json(raw)
            verify_external(envelope, (self.gateway.external / "verification.key").read_bytes())
            entry["artifact"] = raw
            entry["view"]["state"] = "EXPORTED"
            entry["view"]["can_approve"] = False
            entry["view"]["events"].append({"event": "승인된 파일 반출 완료", "time": time.time()})
            return {"state": "EXPORTED", "filename": f"clinical-research-{data['analysis_id'][:8]}.json",
                    "artifact_b64": base64.b64encode(raw).decode("ascii"),
                    "verification_key_b64": base64.b64encode((self.gateway.external / "verification.key").read_bytes()).decode("ascii"),
                    "view": copy.deepcopy(entry["view"])}

    def invalidate(self, identifier, data):
        exact_schema(data, {"analysis_id"})
        with self.lock:
            entry = self._entry(identifier, data["analysis_id"])
            if entry["view"]["state"] in {"REVIEW_READY", "APPROVED"}:
                target = self.llm if entry.get("engine") == "llm" else self
                target.gateway.revoke(data["analysis_id"], target.tokens["reviewer"])
            entry["view"]["state"] = "INVALIDATED"
            entry["view"]["can_approve"] = False
            return {"state": "INVALIDATED"}
