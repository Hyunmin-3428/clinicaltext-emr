"""Stage-by-stage live GPU audit on authored synthetic notes only.

Uses the real web HTTP handlers, real detectors and real model calls. Records
failures as failures, and separately exercises broken clinical/privacy outputs.
"""
from __future__ import annotations
import argparse
import base64
import copy
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from ClinicalCDSGateway.gateway.common import Denied
from ClinicalCDSGateway.gateway.engine import verify_external
from ClinicalTextWeb.audit_detection import run as audit_detection
from ClinicalTextWeb.live_validation import CASES
from ClinicalTextWeb.llm_client import ModelConfig, StructuredClient
from ClinicalTextWeb.llm_pipeline import CLINICAL_SCHEMA, PRIVACY_SCHEMA, audit_evidence, normalize_privacy
from ClinicalTextWeb.prompts import VERIFY_INSTRUCTIONS, PRIVACY_INSTRUCTIONS
from ClinicalTextWeb.server import LocalServer, local_model_config
from ClinicalTextWeb.service import DemoService
from ClinicalTextWeb.risk_candidates import detect_for_research
from ClinicalTextWeb.temporal_policy import POLICY_VERSION
from ClinicalTextWeb.clinical import verify_relations
from ClinicalTextDeid.detectors import detect


def evaluate_fault(client, case, bundle, original_output, damaged, *, privacy=False):
    """Keep model failure distinct from independently detected clinical damage."""
    started=time.perf_counter()
    result={"output":damaged,"mutation_applied":damaged!=original_output,"model_completed":False,"llm_flagged":None}
    code=verify_relations(case["text"],damaged,case["checks"],case["purpose"],[])
    leaked=[s["type"] for s in detect(case["text"],detector="hybrid")
            if s["type"] in {"PERSON","RRN","PHONE","EMAIL","MRN"} and case["text"][s["start"]:s["end"]] in damaged]
    result["independent_code_flagged"]=bool(code["failed"] or leaked)
    try:
        if privacy:
            response=normalize_privacy(client.complete("privacy",PRIVACY_INSTRUCTIONS,{"candidate":damaged,"approved_placeholders":[]},PRIVACY_SCHEMA),damaged)
            audit=copy.deepcopy(bundle["audit"]);audit["residual_phi"]=response["residual_phi"]
            result["llm_flagged"]=bool(response["residual_phi"] or response["uncertainties"])
        else:
            audit=client.complete("verify",VERIFY_INSTRUCTIONS,{"purpose":case["purpose"],"original":case["text"],"output":damaged,"conditions":bundle["plan"]["conditions"],"protected_diagnoses":bundle["plan"]["diagnoses"],"required_phrases":[]},CLINICAL_SCHEMA)
            audit["residual_phi"]=[];response=audit
            result["llm_flagged"]=any(c["status"]!="passed" for c in audit["checks"]) or any(audit[k] for k in ["omissions","additions","contradictions","uncertainties"])
        result["model_completed"]=True;result["audit"]=response
        validation=audit_evidence(case["text"],damaged,case["purpose"],case["checks"],[],bundle["plan"],audit,applied_edits=bundle["applied_edits"])
        result["validation"]=validation
        result["passed"]=result["mutation_applied"] and not validation["eligible"]
    except Denied as error:
        result["model_error"]=error.code
        # An uncompleted model response is never called a semantic detection.
        result["passed"]=result["mutation_applied"] and result["independent_code_flagged"]
    result["seconds"]=round(time.perf_counter()-started,2)
    return result


def run(output_dir):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    cfg=local_model_config()
    if not cfg or cfg["base_url"] != "http://127.0.0.1:11435":
        raise RuntimeError("This audit requires the configured SSH GPU endpoint; CPU inference is refused.")
    report={"policy_version":POLICY_VERSION,"checked_at_utc":datetime.now(timezone.utc).isoformat(),"scope":"Live GPU + real HTTP + authored synthetic notes only; no training", "model":cfg["model"],"cases":{},"faults":{}}
    def save():
        (output_dir/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    detector_report=audit_detection(output_dir/"detectors.json")
    report["detectors"]=detector_report["summary"];save()
    traces=[]
    class Tracer(StructuredClient):
        def complete(self,stage,instructions,payload,schema):
            result=super().complete(stage,instructions,payload,schema)
            traces.append({"stage":stage,"metrics":self.metrics[-1],"result":copy.deepcopy(result)})
            return result
    with tempfile.TemporaryDirectory(prefix="clinical-live-audit-") as root:
        service=DemoService(root,local_model_config=cfg);service.llm.client_factory=Tracer
        server=LocalServer(service,0)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        opener=build_opener(HTTPCookieProcessor(CookieJar()))
        base=f"http://127.0.0.1:{server.server_port}"
        csrf=""
        def call(path,payload=None):
            request=Request(base+path) if payload is None else Request(base+path,data=json.dumps(payload,ensure_ascii=False).encode(),headers={"Content-Type":"application/json","X-CSRF-Token":csrf},method="POST")
            with opener.open(request,timeout=1200) as response:return json.load(response)
        def denied(path,payload):
            try:call(path,payload)
            except HTTPError as error:
                body=json.load(error)
                return {"passed":400<=error.code<500,"code":body.get("error"),"status":error.code}
            return {"passed":False,"code":"UNEXPECTED_SUCCESS"}
        bundles={}
        try:
            bootstrap=call("/api/bootstrap");csrf=bootstrap["csrf"]
            report["bootstrap_gpu_default"] = bootstrap["llm"]["base_url"] == cfg["base_url"] and bootstrap["llm"]["model"] == cfg["model"]
            print("LIVE GPU probe",flush=True)
            report["connection"]=call("/api/model/probe",{})
            gpu_ps=json.load(urlopen(cfg["base_url"]+"/api/ps"))
            report["gpu_loaded"]=gpu_ps
            report["fully_on_gpu"]=any(m["name"]==cfg["model"] and m["size_vram"]==m["size"] and m["size_vram"]>0 for m in gpu_ps["models"])
            save()
            cases=copy.deepcopy(CASES)
            # Same source, deliberately different preservation requirement.
            cases["surrogate_exact_age"]=copy.deepcopy(cases["surrogate"])
            cases["surrogate_exact_age"].update({"purpose":"심혈관 가족력 연구. 환자의 정확한 나이와 부친의 심근경색 사망 나이는 보존한다. 실제 수술 날짜와 수술 후 경과, 부정 표현, 운동 후 무릎 통증을 유지한다. 이름·기관명·직함·행사명은 일반화한다.","checks":["measurements","family","time","treatment","negation"],"required_phrases":["47세"],"private":[v for v in cases["surrogate"]["private"] if v!="47세"]})
            for name in ["surrogate","surrogate_exact_age","bilingual","surgery","neonatal"]:
                case=cases[name];start=time.perf_counter();trace_start=len(traces)
                print("LIVE HTTP "+name,flush=True)
                result={"source":case["text"],"purpose":case["purpose"]}
                try:
                    view=call("/api/analyze",{k:case[k] for k in ["text","purpose","checks","required_phrases"]}|{"engine":"llm","synthetic_consent":False})
                    result["seconds"]=round(time.perf_counter()-start,2);result["view"]=view
                    result["clinical_phrases_preserved"]=all(v in view["output"] for v in case["expected"])
                    result["private_phrases_removed"]=all(v not in view["output"] for v in case["private"])
                    result["natural_generalization"]=name not in {"surrogate","surrogate_exact_age"} or ("교수" not in view["output"] and "[연령]" not in view["output"] and "장거리 운동" in view["output"])
                    entry=next(s.analyses[view["analysis_id"]] for s in service.sessions.values() if view["analysis_id"] in s.analyses)
                    bundles[name]={"case":case,"prepared":copy.deepcopy(entry["evidence"]["bundle"]),"output":view["output"]}
                    gateway=service.llm.gateway;tokens=service.llm.tokens
                    result["internal_original_exact"]=gateway.restore_internal(view["analysis_id"],tokens["reviewer"])==case["text"].encode()
                    try:gateway.restore_internal(view["analysis_id"],tokens["exporter"]);result["unauthorized_restore_denied"]=False
                    except Denied:result["unauthorized_restore_denied"]=True
                    approval={"analysis_id":view["analysis_id"],"review_digest":view["review_digest"],"confirmed":True}
                    result["before_approval_export_denied"]=denied("/api/export",approval)
                    result["tampered_review_denied"]=denied("/api/approve",{**approval,"review_digest":"0"*64})
                    if view["can_approve"]:
                        call("/api/approve",approval)
                        artifact=call("/api/export",approval)
                        raw=base64.b64decode(artifact["artifact_b64"]);key=base64.b64decode(artifact["verification_key_b64"])
                        envelope=json.loads(raw);public=verify_external(envelope,key)
                        result["signed_export_verified"]=public["text"]==view["output"]
                        result["private_export_fields_absent"]=not any(k in public for k in ["source","mapping","capsule","evidence","api_key"])
                        altered=copy.deepcopy(envelope);altered["payload"]["text"]+=" altered"
                        try:verify_external(altered,key);result["altered_export_rejected"]=False
                        except Denied:result["altered_export_rejected"]=True
                        result["second_export_denied"]=denied("/api/export",approval)
                        (output_dir/f"{name}.public.json").write_bytes(raw)
                        (output_dir/f"{name}.verification.key").write_bytes(key)
                    else:result["signed_export_verified"]=False
                    result["passed"]=all(result.get(k,False) for k in ["clinical_phrases_preserved","private_phrases_removed","natural_generalization","internal_original_exact","unauthorized_restore_denied","signed_export_verified","private_export_fields_absent","altered_export_rejected"]) and all(result.get(k,{}).get("passed",False) for k in ["before_approval_export_denied","tampered_review_denied","second_export_denied"])
                except Exception as error:
                    result.update({"passed":False,"error":type(error).__name__,"seconds":round(time.perf_counter()-start,2)})
                    if isinstance(error,HTTPError):result["api_error"]=json.load(error)
                result["model_trace"]=copy.deepcopy(traces[trace_start:])
                report["cases"][name]=result;save()
                print(json.dumps({"case":name,"passed":result["passed"],"seconds":result["seconds"],"error":result.get("api_error")},ensure_ascii=True),flush=True)
            if "surrogate" in bundles:
                item=bundles["surrogate"];case=item["case"];bundle=item["prepared"]
                variants={"negation":item["output"].replace("발열은 없었고","발열이 있었고"),
                          "interval":item["output"].replace("수술 3일 후","수술 5일 후"),
                          "experiencer":item["output"].replace("부친이 52세","환자가 52세"),
                          "unsupported_recency":item["output"].replace("과거력상","과거력상 최근"),
                          "date_removed":item["output"].replace("2023년 3월 10일","[수술일]"),
                          "date_shifted":item["output"].replace("2023년 3월 10일","2023년 3월 11일"),
                          "history_year_shifted":item["output"].replace("2018년","2020년")}
                for name,damaged in variants.items():
                    print("LIVE fault "+name,flush=True)
                    report["faults"][name]=evaluate_fault(StructuredClient(ModelConfig.parse(cfg)),case,bundle,item["output"],damaged);save()
                leaked="김민수. "+item["output"]
                print("LIVE fault identity_leak",flush=True)
                report["faults"]["identity_leak"]=evaluate_fault(StructuredClient(ModelConfig.parse(cfg)),case,bundle,item["output"],leaked,privacy=True);save()
            report["successful"]=report["fully_on_gpu"] and report["bootstrap_gpu_default"] and all(c["passed"] for c in report["cases"].values()) and len(report["cases"])==5 and all(c["passed"] for c in report["faults"].values()) and len(report["faults"])==8
            save()
        finally:
            server.shutdown();server.server_close();worker.join()
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir",default="ClinicalTextWeb/.runtime/stage-audit")
    args=parser.parse_args()
    result=run(args.out_dir)
    raise SystemExit(0 if result["successful"] else 1)
