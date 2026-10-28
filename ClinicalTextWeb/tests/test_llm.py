"""Protocol and adversarial tests with explicitly simulated model responses.

These tests do NOT measure the performance of a real language model.
"""
import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from ClinicalCDSGateway.gateway.common import Denied
from ClinicalCDSGateway.gateway.engine import verify_external
from ClinicalTextDeid.api import transform
from ClinicalTextWeb.llm_client import ModelConfig, StructuredClient
from ClinicalTextWeb.llm_pipeline import apply_edits, audit_evidence, PurposePipeline, redaction_markup_only, checked_age_generalization, normalize_privacy
from ClinicalTextWeb.privacy_policy import introduction_policy, introduction_issues, retain_required_age
from ClinicalTextWeb.risk_candidates import detect_for_research
from ClinicalTextWeb.temporal_policy import clinical_dates, date_rows, permitted_calendar_occurrences, preserved_date_candidate
from ClinicalTextWeb.llm_service import SURGERY, NEONATAL
from ClinicalTextWeb.service import DemoService

CONTEXT = {"project": "test", "document": "test", "release": "test", "purpose": "research"}


def plan():
    return {"conditions": [{"id": "surgery", "relation": "충수절제술 시행 여부", "source_quote": "충수절제술 시행"},
                            {"id": "course", "relation": "수술 3일 후 발열 부정", "source_quote": "수술 3일 후에도 발열 없음"}],
            "diagnoses": [], "edits": [
                {"quote": "김민수", "occurrence": 0, "type": "PERSON", "replacement": "<PERSON_1>", "reason": "환자 이름"}], "uncertainties": []}


def audit():
    return {"checks": [{"id": item["id"], "status": "passed", "source_quote": item["source_quote"],
                        "output_quote": item["source_quote"], "explanation": "대응 근거에서 임상관계를 유지함"} for item in plan()["conditions"]],
            "residual_phi": [], "omissions": [], "additions": [], "contradictions": [], "uncertainties": []}


class SimulatedClient:
    def __init__(self, config=None, planner=None, auditor=None):
        self.calls = 0
        self.inputs = []
        self.planner = planner or plan()
        self.auditor = auditor or audit()

    def complete(self, stage, instructions, payload, schema):
        self.calls += 1
        self.inputs.append((stage, copy.deepcopy(payload)))
        if stage == "plan":
            return copy.deepcopy(self.planner)
        if stage == "privacy":
            return {"assessments":[{**item,"classification":"identifier"} for item in copy.deepcopy(self.auditor["residual_phi"])],"uncertainties":[]}
        return copy.deepcopy({k:v for k,v in self.auditor.items() if k != "residual_phi"})


class DiagnosisTests(unittest.TestCase):
    def test_actual_hybrid_preserves_neonatal_diagnoses_and_masks_name(self):
        result = transform({"text": NEONATAL, "context": CONTEXT, "detector": "hybrid"}, key=os.urandom(32))
        output = result["public"]["text"]
        for phrase in ["Respiratory failure of newborn", "Metabolic acidosis", "Hypoxic ischaemic encephalopathy of newborn"]:
            self.assertIn(phrase, output)
        self.assertNotIn("김민준", output)
        self.assertTrue(result["assessment"]["semantic_checks"]["clinical_lexical_guard_checks"]["clinical_diagnosis"])

    def test_name_anchor_still_wins_over_diagnosis_phrase(self):
        result = transform({"text": "Name: Metabolic Acidosis", "context": CONTEXT, "detector": "hybrid"}, key=os.urandom(32))
        self.assertNotIn("Metabolic Acidosis", result["public"]["text"])
        self.assertEqual(result["assessment"]["semantic_checks"]["status"], "failed")

    def test_diagnosis_damage_detected_even_when_not_selected(self):
        from ClinicalTextWeb.clinical import verify_relations
        source = "Respiratory failure of newborn. BP 120/80 mmHg."
        damaged = source.replace("Respiratory", "<PERSON_1>")
        result = verify_relations(source, damaged, ["measurements"], "혈압 연구", [])
        self.assertGreater(result["failed"], 0)
        self.assertFalse(result["eligible"])


class PipelineTests(unittest.TestCase):
    def test_privacy_classification_and_quote_grounding_keep_risks_and_generic_facts_separate(self):
        output="성명: 박하늘. 본원에서 2022-08-09 입원. 29세 교육기관 종사자."
        result=normalize_privacy({"assessments":[
            {"quote":"박하늘","classification":"identifier","type":"PERSON","reason":"성명"},
            {"quote":"본원","classification":"clinical_or_generic","type":"ORGANIZATION","reason":"일반 지칭"},
            {"quote":"29세 교육기관 종사자","classification":"combination_risk","type":"QUASI","reason":"조합 검토"},
            {"quote":"2022-08-09","classification":"uncertain","type":"DATE","reason":"날짜 확인"}],"uncertainties":[]},output)
        self.assertEqual([r["quote"] for r in result["residual_phi"]],["박하늘","29세 교육기관 종사자"])
        self.assertEqual(result["uncertainties"],["날짜 확인"])
        ungrounded=normalize_privacy({"assessments":[{"quote":"원문에 없는 정보","classification":"clinical_or_generic","type":"OTHER","reason":"오류"}],"uncertainties":[]},output)
        self.assertTrue(ungrounded["uncertainties"])

    def test_history_year_stays_bound_to_symptom_after_event_generalization(self):
        source="2016년 부산마라톤 완주 후 무릎 통증이 있었다."
        proposal={"conditions":[{"id":"c","relation":"운동 후 통증과 시점","source_quote":source}],"diagnoses":[],"uncertainties":[],
                  "edits":[{"quote":"부산마라톤","occurrence":0,"type":"EVENT","replacement":"장거리 운동","reason":"행사 일반화"}]}
        output,_=apply_edits(source,proposal,[])
        self.assertIn("2016년",output)
        self.assertIn("장거리 운동",output)
        self.assertTrue(permitted_calendar_occurrences(source,output))
        self.assertFalse(permitted_calendar_occurrences(source,output.replace("2016년","2017년")))
        compound=copy.deepcopy(proposal)
        compound["edits"][0].update({"quote":"2016년 부산마라톤","replacement":"2016년 장거리 운동"})
        self.assertEqual(apply_edits(source,compound,[])[0],output)
        proposal["edits"].append({"quote":"2016년","occurrence":0,"type":"DATE","replacement":"","reason":"잘못된 연도 삭제"})
        with self.assertRaises(Denied):apply_edits(source,proposal,[])

    def test_unclear_calendar_date_is_kept_and_held_instead_of_silently_erased(self):
        source="기록일: 2024년 8월 2일. BP 120/80 mmHg."
        proposal={"conditions":[{"id":"c","relation":"혈압","source_quote":"BP 120/80 mmHg"}],"diagnoses":[],"uncertainties":[],"edits":[]}
        output,edits=apply_edits(source,proposal,[])
        response={"checks":[{"id":"c","status":"passed","source_quote":"BP 120/80 mmHg","output_quote":"BP 120/80 mmHg","explanation":"보존"}],"residual_phi":[],"omissions":[],"additions":[],"contradictions":[],"uncertainties":[]}
        self.assertIn("2024년 8월 2일",output)
        self.assertFalse(audit_evidence(source,output,"혈압 연구",["measurements"],[],proposal,response,applied_edits=edits)["eligible"])
        proposal["edits"]=[{"quote":"2024년 8월 2일","occurrence":0,"type":"DATE","replacement":"[날짜]","reason":"불명확한 날짜"}]
        with self.assertRaises(Denied):apply_edits(source,proposal,[])

    def test_date_detector_resolution_requires_clinical_event_and_correct_type(self):
        source="생년월일: 1971-01-01. 2022-08-09 입원."
        output="2022-08-09 입원."
        candidate={"start":0,"end":10,"type":"DT_BIRTH"}
        self.assertTrue(preserved_date_candidate(source,output,candidate))
        self.assertFalse(preserved_date_candidate(source,output,{**candidate,"type":"PERSON"}))
        self.assertFalse(preserved_date_candidate(source,"1971-01-01. "+output,candidate))

    def test_fault_audit_retains_incomplete_model_responses_and_continues(self):
        from ClinicalTextWeb.audit_pipeline import evaluate_fault
        class Incomplete:
            def complete(self,*args):raise Denied("LLM_RESPONSE_INCOMPLETE")
        output,edits=apply_edits(SURGERY,plan(),[])
        bundle={"plan":plan(),"audit":audit(),"applied_edits":edits}
        case={"text":SURGERY,"purpose":"수술 후 감염 연구","checks":["time","negation","treatment"]}
        for changed in [output.replace("발열 없음","발열 있음"),output.replace("수술 3일 후","수술 5일 후")]:
            result=evaluate_fault(Incomplete(),case,bundle,output,changed)
            self.assertTrue(result["passed"])
            self.assertTrue(result["independent_code_flagged"])
            self.assertFalse(result["model_completed"])
            self.assertIsNone(result["llm_flagged"])
        self.assertFalse(evaluate_fault(Incomplete(),case,bundle,output,output)["passed"])

    def test_surgery_date_is_protected_even_when_model_calls_it_identifier(self):
        source = SURGERY.replace("3월 10일", "2023년 3월 10일")
        for replacement in ["[수술일]", "", "2023년 3월 11일"]:
            proposal = plan()
            proposal["edits"].append({"quote":"2023년 3월 10일", "occurrence":0, "type":"DATE", "replacement":replacement, "reason":"잘못된 날짜 처리"})
            with self.subTest(replacement=replacement), self.assertRaises(Denied) as caught:
                apply_edits(source, proposal, [])
            self.assertEqual(caught.exception.code, "LLM_CLINICAL_EDIT_DENIED")

    def test_code_detects_surgery_date_loss_even_when_llm_audit_passes(self):
        source = SURGERY.replace("3월 10일", "2023년 3월 10일")
        output, edits = apply_edits(source, plan(), [])
        self.assertIn("2023년 3월 10일", output)
        for date in ["[수술일]", "2023년 3월 11일", ""]:
            damaged = output.replace("2023년 3월 10일", date)
            with self.subTest(date=date):
                result = audit_evidence(source, damaged, "수술 후 감염 연구", ["time"], [], plan(), audit(), applied_edits=edits)
                self.assertFalse(result["eligible"])
                self.assertTrue(any(r["kind"]=="clinical_date" and r["status"]=="failed" for r in result["rows"]))

    def test_dates_must_stay_linked_to_original_clinical_events(self):
        source = "입원일: 2023-03-10. 퇴원일: 2023-03-15."
        swapped = "입원일: 2023-03-15. 퇴원일: 2023-03-10."
        self.assertTrue(permitted_calendar_occurrences(source,source))
        self.assertFalse(permitted_calendar_occurrences(source,swapped))
        self.assertTrue(all(r["status"]=="failed" for r in date_rows(source,swapped)))

    def test_birth_date_is_not_treated_as_surgery_date(self):
        source = "생년월일: 1976-04-02. 2023-03-10 충수절제술 시행. 수술 3일 후 발열 없음."
        proposal = plan()
        proposal["edits"] = []
        proposal["conditions"] = [{"id":"c", "relation":"수술", "source_quote":"충수절제술 시행"}]
        self.assertEqual([d["quote"] for d in clinical_dates(source)], ["2023-03-10"])
        output, _ = apply_edits(source,proposal,[])
        self.assertNotIn("1976-04-02",output)
        self.assertIn("2023-03-10",output)
        self.assertIn("수술 3일 후",output)
        self.assertFalse(permitted_calendar_occurrences(source,output+" 1976-04-02"))

    def test_elapsed_days_are_checked_even_if_time_option_is_not_selected(self):
        from ClinicalTextWeb.clinical import verify_relations
        result = verify_relations("수술 3일 후 발열 없음.", "수술 5일 후 발열 없음.", ["negation"], "연구", [])
        self.assertTrue(any(r["kind"]=="time" and r["status"]=="failed" for r in result["rows"]))

    def test_privacy_checker_does_not_receive_original_or_source_quotes(self):
        client=SimulatedClient()
        PurposePipeline(client).run(SURGERY,"수술 후 감염 연구",["time"],[],[])
        privacy=next(payload for stage,payload in client.inputs if stage=="privacy")
        self.assertEqual(set(privacy),{"candidate","approved_placeholders"})
        self.assertNotIn("김민수",str(privacy))

    def test_wrong_age_band_and_age_date_confusion_are_rejected(self):
        self.assertEqual(checked_age_generalization("47세","40~64세의 직장인"),"중년의 직장인")
        with self.assertRaises(Denied):checked_age_generalization("47세","고령 성인")
        proposal=plan();proposal["edits"]=[{"quote":"47세","occurrence":0,"type":"DATE","replacement":"[연령]","reason":"잘못된 날짜 판정"}]
        proposal["conditions"]=[{"id":"c","relation":"혈압","source_quote":"BP 120/80 mmHg"}]
        with self.assertRaises(Denied):apply_edits("47세. BP 120/80 mmHg.",proposal,[])

    def test_compound_generalization_subsumes_only_strictly_nested_identifiers(self):
        source="박하늘(29세, 가람대학교 강사). BP 120/80 mmHg."
        proposal={"conditions":[{"id":"c","relation":"혈압","source_quote":"BP 120/80 mmHg"}],"diagnoses":[],"uncertainties":[],"edits":[
            {"quote":"박하늘(29세, 가람대학교 강사)","occurrence":0,"type":"QUASI","replacement":"청년 성인의 교육기관 종사자","reason":"복합 일반화"},
            {"quote":"가람대학교","occurrence":0,"type":"ORGANIZATION","replacement":"","reason":"내부 기관 제거"}]}
        output,edits=apply_edits(source,proposal,[])
        self.assertEqual(len(edits),1)
        self.assertEqual(output,"청년 성인의 교육기관 종사자. BP 120/80 mmHg.")

    def test_introduction_policy_keeps_family_and_patient_age_roles_separate(self):
        from ClinicalTextWeb.llm_service import SURROGATE,SURROGATE_PURPOSE
        policies=introduction_policy(SURROGATE,SURROGATE_PURPOSE,[])
        self.assertEqual(len(policies),1)
        self.assertEqual(policies[0]["patient_age_quote"],"47세")
        p=policies[0]
        edits=[{"start":p["start"],"end":p["end"],"replacement":"중년의 교육기관 종사자"}]
        self.assertEqual(introduction_issues(SURROGATE,edits,policies),[])
        exact=introduction_policy(SURROGATE,"환자의 정확한 나이는 보존한다.",["47세"])
        self.assertTrue(introduction_issues(SURROGATE,edits,exact))

    def test_required_age_is_restored_only_from_the_patient_introduction(self):
        text="박하늘(29세, 가람대학교 강사)는 수술을 받았다. 모친이 68세에 사망했다."
        policies=introduction_policy(text,"환자의 나이는 보존한다.",["29세"])
        p=policies[0]
        edits=[{"start":p["start"],"end":p["end"],"type":"QUASI","replacement":"청년 성인의 교육기관 종사자"}]
        changes=retain_required_age(edits,policies)
        self.assertEqual(edits[0]["replacement"],"29세의 교육기관 종사자")
        self.assertEqual(len(changes),1)
        self.assertEqual(introduction_issues(text,edits,policies),[])

    def test_reintroduced_known_name_is_blocked_when_llm_reports_no_leak(self):
        source="성명: 김민수. BP 120/80 mmHg."
        proposal={"conditions":[{"id":"c","relation":"혈압","source_quote":"BP 120/80 mmHg"}],"diagnoses":[],"uncertainties":[],"edits":[{"quote":"김민수","occurrence":0,"type":"PERSON","replacement":"<PERSON_1>","reason":"이름"}]}
        output,edits=apply_edits(source,proposal,[])
        report={"checks":[{"id":"c","status":"passed","source_quote":"BP 120/80 mmHg","output_quote":"BP 120/80 mmHg","explanation":"보존"}],"residual_phi":[],"omissions":[],"additions":[],"contradictions":[],"uncertainties":[]}
        result=audit_evidence(source,"김민수. "+output,"혈압 연구",["measurements"],[],proposal,report,applied_edits=edits)
        self.assertFalse(result["eligible"])

    def test_research_candidates_cover_context_without_treating_lab_values_as_phi(self):
        text="성명: 박하늘. 서울대학교병원에서 2022년 4월 2일 진료. 2020년 부산마라톤 완주. 부친이 58세. Cr 1.2 mg/dL."
        spans=detect_for_research(text)
        values={text[s["start"]:s["end"]] for s in spans}
        for value in ["박하늘","서울대학교병원","2022년 4월 2일","2020년","부산마라톤","58세"]:self.assertIn(value,values)
        self.assertFalse(any(s["start"]<len(text) and "Cr 1.2" in text[s["start"]:s["end"]] for s in spans))
        self.assertTrue(any(s.get("role_hint")=="family_age" for s in spans))

    def test_omitted_surgery_date_edit_leaves_absolute_date_and_elapsed_days(self):
        proposal = plan()
        proposal["edits"] = [proposal["edits"][0]]
        output, edits = apply_edits(SURGERY, proposal, [])
        self.assertIn("3월 10일", output)
        self.assertIn("수술 3일 후에도 발열 없음", output)
        self.assertTrue(audit_evidence(SURGERY, output, "수술 후 감염 연구", ["time"], [], proposal, audit(), applied_edits=edits)["eligible"])

    def test_placeholder_warning_requires_provenance_and_never_waives_real_name(self):
        proposal = plan()
        output, edits = apply_edits(SURGERY, proposal, [])
        report = audit()
        report["residual_phi"] = [{"quote": "<PERSON_1>", "type": "PERSON", "reason": "이름 후보"}]
        self.assertFalse(audit_evidence(SURGERY, output, "연구", ["time"], [], proposal, report)["eligible"])
        self.assertTrue(audit_evidence(SURGERY, output, "연구", ["time"], [], proposal, report, applied_edits=edits)["eligible"])
        report["residual_phi"][0]["quote"] = "김민수 <PERSON_1>"
        self.assertFalse(audit_evidence(SURGERY, output, "연구", ["time"], [], proposal, report, applied_edits=edits)["eligible"])

    def test_generated_placeholders_are_distinguished_from_real_identifiers(self):
        tokens={"<PERSON_1>","<PERSON_2>","[수술일]"}
        self.assertTrue(redaction_markup_only("성명: <PERSON_1>. Patient name: <PERSON_2>",tokens))
        self.assertTrue(redaction_markup_only("[수술일]",tokens))
        for value in ["김민수 <PERSON_1>", "Name: John Smith <PERSON_2>", "<UNKNOWN_1>", "<PERSON_1> failure of newborn"]:
            self.assertFalse(redaction_markup_only(value,tokens))

    def test_different_names_cannot_collapse_into_a_shared_model_token(self):
        source="Alice Park met John Smith."
        proposal={"conditions":[{"id":"c1","relation":"만남","source_quote":"met"}],"diagnoses":[],"uncertainties":[],"edits":[
            {"quote":"Alice Park","occurrence":0,"type":"PERSON","replacement":"<PERSON_1>","reason":"이름"},
            {"quote":"John Smith","occurrence":0,"type":"PERSON","replacement":"<PERSON_1>","reason":"이름"}]}
        output,edits=apply_edits(source,proposal,[])
        self.assertEqual(output,"<PERSON_1> met <PERSON_2>.")

    def test_purpose_planning_and_separate_grounded_verification(self):
        client = SimulatedClient()
        result = PurposePipeline(client).run(SURGERY, "수술 후 감염 연구", ["time", "negation", "treatment"], [], [])
        self.assertEqual([item[0] for item in client.inputs], ["plan", "verify", "privacy"])
        self.assertEqual(client.inputs[0][1]["purpose"], "수술 후 감염 연구")
        self.assertTrue(result["validation"]["eligible"])
        self.assertIn("3월 10일", result["output"])
        self.assertIn("수술 3일 후에도 발열 없음", result["output"])
        self.assertNotIn("김민수", result["output"])

    def test_diagnosis_edit_is_rejected_not_exempted_by_model(self):
        proposal = plan()
        proposal["conditions"] = [{"id": "dx", "relation": "진단", "source_quote": "Metabolic acidosis"}]
        proposal["edits"] = [{"quote": "Metabolic", "occurrence": 0, "type": "PERSON", "replacement": "<PERSON_1>", "reason": "잘못된 이름 판정"}]
        client = SimulatedClient(planner=proposal)
        with self.assertRaises(Denied) as caught:
            PurposePipeline(client).run("Metabolic acidosis", "신생아 연구", ["measurements"], [], [])
        self.assertEqual(caught.exception.code, "LLM_CLINICAL_EDIT_DENIED")
        self.assertEqual(client.calls, 2)

    def test_hallucinated_evidence_cannot_pass(self):
        output, _ = apply_edits(SURGERY, plan(), [])
        for case in ["output", "source", "missing", "duplicate"]:
            report = audit()
            if case == "output": report["checks"][0]["output_quote"] = "원문과 결과에 없는 근거"
            if case == "source": report["checks"][0]["source_quote"] = "다른 원문 근거"
            if case == "missing": report["checks"].pop()
            if case == "duplicate": report["checks"].append(copy.deepcopy(report["checks"][0]))
            with self.subTest(case=case):
                validation = audit_evidence(SURGERY, output, "수술 후 감염 연구", ["time", "negation", "treatment"], [], plan(), report)
                self.assertFalse(validation["eligible"])

    def test_global_omission_and_privacy_audit_block(self):
        output, _ = apply_edits(SURGERY, plan(), [])
        for key in ["omissions", "contradictions", "uncertainties"]:
            report = audit()
            report[key] = ["제공한 관계 목록 밖의 문제"]
            self.assertFalse(audit_evidence(SURGERY, output, "연구", ["time"], [], plan(), report)["eligible"])
        report = audit()
        report["residual_phi"] = [{"quote": "<PERSON_1>", "type": "PERSON", "reason": "확인 필요"}]
        self.assertFalse(audit_evidence(SURGERY, output, "연구", ["time"], [], plan(), report)["eligible"])

    def test_bad_edit_offsets_and_overlaps_rejected(self):
        for mode in ["missing", "occurrence", "overlap"]:
            proposal = plan()
            if mode == "missing": proposal["edits"][0]["quote"] = "없는 이름"
            if mode == "occurrence": proposal["edits"][0]["occurrence"] = 2
            if mode == "overlap": proposal["edits"].append(copy.deepcopy(proposal["edits"][0]))
            with self.subTest(mode=mode), self.assertRaises(Denied):
                apply_edits(SURGERY, proposal, [])

    def test_direct_identifier_cannot_be_preserved_by_omission(self):
        proposal = plan()
        proposal["edits"] = []
        start = SURGERY.index("김민수")
        output, _ = apply_edits(SURGERY, proposal, [{"start": start, "end": start+3, "type": "PERSON", "method": "clinical_regex"}])
        self.assertNotIn("김민수", output)

    def test_failed_audit_retries_only_from_original_and_still_holds(self):
        report = audit()
        report["uncertainties"] = ["임상관계 확인 불가"]
        client = SimulatedClient(auditor=report)
        result = PurposePipeline(client).run(SURGERY, "연구", ["time"], [], [])
        self.assertEqual(client.calls, 6)
        self.assertEqual([p["original"] for stage, p in client.inputs if stage == "plan"], [SURGERY, SURGERY])
        self.assertFalse(result["validation"]["eligible"])


class LLMServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="clinical-llm-test-")
        self.service = DemoService(self.temp.name)
        self.service.llm.client_factory = SimulatedClient
        self.owner, _ = self.service.new_session()
        self.config = {"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "simulated-test-model", "verifier_model": "", "api_key": ""}
        self.input = {"purpose": "수술 후 감염 연구", "text": SURGERY, "checks": ["time", "treatment", "negation"], "required_phrases": [], "engine": "llm", "synthetic_consent": False}

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_model_does_not_fall_back_to_rules(self):
        with self.assertRaises(Denied) as caught:
            self.service.analyze(self.owner, self.input)
        self.assertEqual(caught.exception.code, "LLM_NOT_CONFIGURED")

    def test_synthetic_llm_result_can_use_existing_signed_release(self):
        self.service.llm.configure(self.owner, self.config)
        view = self.service.analyze(self.owner, self.input)
        self.assertTrue(view["can_approve"], view["reasons"])
        self.assertEqual(view["llm"]["calls"], 3)
        approval = {"analysis_id": view["analysis_id"], "review_digest": view["review_digest"], "confirmed": True}
        self.service.approve(self.owner, approval)
        result = self.service.export(self.owner, approval)
        raw = base64.b64decode(result["artifact_b64"])
        public = verify_external(json.loads(raw), base64.b64decode(result["verification_key_b64"]))
        self.assertIn("3월 10일", public["text"])
        self.assertNotIn("김민수".encode(), raw)
        self.assertNotIn(b'"evidence"', raw)
        self.assertNotIn(b'"api_key"', raw)

    def test_validated_iso_surgery_date_survives_gateway_revalidation_and_export(self):
        from ClinicalTextWeb.llm_service import AUTHORED_SOURCES
        from ClinicalCDSGateway.gateway.common import digest
        for date in ["2023-03-10","2023년 3월 10일"]:
            text = SURGERY.replace("3월 10일", date)
            with self.subTest(date=date), patch("ClinicalTextWeb.llm_service.AUTHORED_SOURCES", AUTHORED_SOURCES | {digest(text)}):
                self.service.llm.configure(self.owner,self.config)
                view = self.service.analyze(self.owner, {**self.input,"text":text})
                self.assertTrue(view["can_approve"], view["reasons"])
                approval={"analysis_id":view["analysis_id"],"review_digest":view["review_digest"],"confirmed":True}
                self.service.approve(self.owner,approval)
                exported=self.service.export(self.owner,approval)
                public=verify_external(json.loads(base64.b64decode(exported["artifact_b64"])),base64.b64decode(exported["verification_key_b64"]))
                self.assertIn(date,public["text"])
                self.assertIn("수술 3일 후",public["text"])

    def test_remote_request_requires_explicit_data_destination_consent(self):
        self.service.llm.configure(self.owner, {**self.config, "provider": "openai", "base_url": "https://api.openai.com/v1", "api_key": "test-not-a-real-key"})
        with self.assertRaises(Denied) as caught:
            self.service.analyze(self.owner, self.input)
        self.assertEqual(caught.exception.code, "LLM_REMOTE_CONSENT_REQUIRED")

    def test_unregistered_source_still_cannot_be_released(self):
        self.service.llm.configure(self.owner, self.config)
        view = self.service.analyze(self.owner, {**self.input, "text": SURGERY + "\n추가 기록."})
        self.assertFalse(view["registered_example"])
        self.assertFalse(view["can_approve"])

    def test_config_does_not_return_key_and_config_change_revokes(self):
        config = {**self.config, "api_key": "secret-for-simulation"}
        self.assertNotIn("api_key", self.service.llm.configure(self.owner, config))
        view = self.service.analyze(self.owner, self.input)
        approval = {"analysis_id": view["analysis_id"], "review_digest": view["review_digest"], "confirmed": True}
        self.service.approve(self.owner, approval)
        self.service.llm.configure(self.owner, {**self.config, "model": "other-model"})
        with self.assertRaises(Denied):
            self.service.export(self.owner, approval)


class ProtocolTests(unittest.TestCase):
    def test_ollama_structured_protocol_over_actual_local_http(self):
        observations = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                observations.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                raw = json.dumps({"message": {"content": '{"ready":true}'}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            config = ModelConfig.parse({"provider": "ollama", "base_url": f"http://127.0.0.1:{server.server_port}", "model": "simulated", "verifier_model": "", "api_key": ""})
            self.assertTrue(StructuredClient(config).probe()["connected"])
            self.assertEqual(observations[0][0], "/api/chat")
            self.assertEqual(observations[0][1]["format"]["type"], "object")
            self.assertFalse(observations[0][1]["stream"])
            self.assertEqual(observations[0][1]["options"]["num_ctx"], 16384)
            self.assertEqual(observations[0][1]["options"]["num_predict"], 4096)
        finally:
            server.shutdown()
            server.server_close()

    def test_external_http_and_embedded_credentials_rejected(self):
        for url in ["http://example.org/v1", "https://user:password@example.org/v1", "https://example.org/v1?token=secret"]:
            with self.subTest(url=url), self.assertRaises(Denied):
                ModelConfig.parse({"provider": "compatible", "base_url": url, "model": "model", "verifier_model": "", "api_key": ""})

    def test_context_limit_fails_before_network_not_silent_truncation(self):
        config = ModelConfig.parse({"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "not-called", "verifier_model": "", "api_key": ""})
        with self.assertRaises(Denied) as caught:
            StructuredClient(config).complete("probe", "x" * 20000, {}, {"type":"object","properties":{},"required":[],"additionalProperties":False})
        self.assertEqual(caught.exception.code, "LLM_CONTEXT_LIMIT")


if __name__ == "__main__":
    unittest.main()
