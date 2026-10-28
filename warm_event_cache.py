"""Bounded one-time synthetic precomputation; no patient data or shared service restart."""
import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen
from ClinicalTextWeb.llm_client import ModelConfig
from ClinicalTextWeb.llm_pipeline import PurposePipeline
from ClinicalTextWeb.prepared_cache import PreparedCache,cached_cases
from ClinicalTextWeb.resource_guard import ResourceGuard,GuardedClient
from ClinicalTextWeb.risk_candidates import detect_for_research

def model_digest(url,model):
    with urlopen(url+'/api/tags',timeout=5) as response:data=json.load(response)
    return next(m['digest'] for m in data['models'] if m['name']==model)

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',required=True);p.add_argument('--model-url',default='http://127.0.0.1:11435');p.add_argument('--model',default='qwen3:30b');a=p.parse_args()
    root=Path(a.runtime);root.mkdir(parents=True,exist_ok=True)
    cfg=ModelConfig('ollama',a.model_url,a.model,a.model)
    cache=PreparedCache(root/'prepared-cache',model_digest(a.model_url,a.model))
    guard=ResourceGuard(root,a.model_url,a.model)
    report=[]
    for case in cached_cases():
        try:
            try:cache.get(case['text'],case['purpose'],case['checks'],[],cfg);status='already_cached'
            except Exception:
                with guard.lease():
                    client=GuardedClient(cfg,guard)
                    prepared=PurposePipeline(client).run(case['text'],case['purpose'],case['checks'],[],detect_for_research(case['text']))
                    cache.put(case['text'],case['purpose'],case['checks'],[],cfg,prepared)
                status='cached'
            row={'record_id':case['id'],'purpose_id':case['purpose_id'],'status':status}
        except Exception as error:row={'record_id':case['id'],'purpose_id':case['purpose_id'],'status':'failed','error':getattr(error,'code',type(error).__name__)}
        report.append(row);(root/'cache-warm-report.json').write_text(json.dumps({'cases':report,'implementation':cache.version,'model_digest':cache.model_digest},indent=2))
        print(json.dumps(row),flush=True)
    if any(r['status']=='failed' for r in report):raise SystemExit(1)

if __name__=='__main__':main()
