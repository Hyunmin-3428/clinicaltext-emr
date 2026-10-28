"""Source-grounded, version-bound cache of real synthetic-example model runs."""
import copy
import hashlib
import json
from pathlib import Path
import time
from ClinicalCDSGateway.gateway.common import canonical, digest, Denied
from .llm_pipeline import audit_evidence

ROOT=Path(__file__).resolve().parent.parent
FILES=['ClinicalTextWeb/prompts.py','ClinicalTextWeb/llm_pipeline.py','ClinicalTextWeb/llm_service.py','ClinicalTextWeb/clinical.py','ClinicalTextWeb/temporal_policy.py','ClinicalTextWeb/privacy_policy.py','ClinicalTextWeb/risk_candidates.py','ClinicalTextWeb/input_text.py','ClinicalTextDeid/clinical_guards.py','ClinicalTextDeid/detectors.py','ClinicalTextDeid/generalize.py','ClinicalTextWeb/synthetic_notes.py','examples/clinical_notes/01_neonatal_admission.txt','examples/clinical_notes/02_neonatal_progress.txt','examples/clinical_notes/03_neonatal_discharge.txt']

def fingerprint():
    return digest(canonical({name:hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in FILES}))

class PreparedCache:
    def __init__(self,directory,model_digest='unverified'):
        self.directory=Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.version=fingerprint()
        self.model_digest=model_digest
    def key(self,source,purpose,checks,phrases,config):
        return digest(canonical({'source':digest(source),'purpose':purpose,'checks':checks,'phrases':phrases,'model':config.model,'verifier_model':config.verifier_model,'model_digest':self.model_digest,'implementation':self.version}))
    def validate(self,source,purpose,checks,phrases,prepared):
        value=audit_evidence(source,prepared['output'],purpose,checks,phrases,prepared['bundle']['plan'],prepared['bundle']['audit'],applied_edits=prepared['edits'])
        if value!=prepared['validation'] or not value['eligible']:raise Denied('DEMO_CACHE_INVALID')
    def get(self,source,purpose,checks,phrases,config):
        path=self.directory/(self.key(source,purpose,checks,phrases,config)+'.json')
        if not path.is_file():raise Denied('DEMO_CACHE_NOT_READY')
        item=json.loads(path.read_text(encoding='utf8'))
        if item['implementation']!=self.version or item['key']!=path.stem:raise Denied('DEMO_CACHE_INVALID')
        prepared=item['prepared']
        self.validate(source,purpose,checks,phrases,prepared)
        result=copy.deepcopy(prepared)
        result.update(cached=True,cache_created_at=item['created'],original_calls=prepared['calls'],calls=0,attempts=0,metrics=[])
        return result
    def put(self,source,purpose,checks,phrases,config,prepared):
        self.validate(source,purpose,checks,phrases,prepared)
        key=self.key(source,purpose,checks,phrases,config)
        item={'key':key,'implementation':self.version,'model_digest':self.model_digest,'created':time.time(),'prepared':prepared}
        temp=self.directory/(key+'.pending')
        temp.write_text(json.dumps(item,ensure_ascii=False),encoding='utf8')
        temp.replace(self.directory/(key+'.json'))

def cached_cases():
    from .service import examples
    from demo_server import PURPOSES
    records=examples()
    cases=[{**r,'purpose_id':'record-default'} for r in records]
    record=next(r for r in records if r['id']=='surrogate')
    cases.append({**record,'purpose':next(p['purpose'] for p in PURPOSES if p['id']=='exact-age'),'purpose_id':'exact-age'})
    return cases
