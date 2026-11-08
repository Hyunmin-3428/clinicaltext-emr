"""One NEW authored synthetic note through the running local Ollama/web flow.

This runs real local inference, unlike validate_local_collaboration.py.
No real patient record or external recipient transfer is used.
"""
import json
import argparse
from pathlib import Path
import time
from urllib.request import Request,urlopen


def negative_control():
    from ClinicalTextWeb.llm_client import ModelConfig
    from ClinicalTextWeb.prompts import VERIFY_INSTRUCTIONS
    from ClinicalTextWeb.llm_pipeline import CLINICAL_SCHEMA
    from run_local_demo import LocalClient
    source='성명: 이도윤. 2024년 6월 12일 충수절제술 시행. 수술 3일 후에도 발열 없음. 수술 부위 감염 소견 없음.'
    output='<PERSON_1>. 2024년 6월 12일 충수절제술 시행. 수술 2일 후 발열 있음. 수술 부위 감염 소견 없음.'
    config=ModelConfig.parse({'provider':'ollama','base_url':'http://127.0.0.1:11434','model':'qwen3:4b-instruct','verifier_model':'qwen3:4b-instruct','api_key':''})
    client=LocalClient(config)
    started=time.monotonic()
    result=client.complete('verify',VERIFY_INSTRUCTIONS,{'purpose':'수술 후 발열·합병증 연구. 수술일, 경과일, 부정 표현을 보존한다.',
        'original':source,'output':output,'conditions':[{'id':'c1','relation':'수술 후 경과일과 발열 부정',
        'source_quote':'수술 3일 후에도 발열 없음'}],'protected_diagnoses':[],'required_phrases':[]},CLINICAL_SCHEMA)
    detected=any(row['id']=='c1' and row['status']=='failed' and row['source_quote']=='수술 3일 후에도 발열 없음' for row in result['checks'])
    return {'scope':'One deliberately damaged synthetic output: elapsed days and negation changed.',
        'passed':detected,'seconds':round(time.monotonic()-started,2),'calls':client.calls,'audit':result,'metrics':client.metrics}


def validate(origin='http://127.0.0.1:8773'):
    token=''
    def call(path,data=None):
        headers={'Authorization':'Bearer '+token} if token else {}
        if data is not None:headers['Content-Type']='application/json'
        with urlopen(Request(origin+path,data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None,headers=headers),timeout=30) as response:
            return json.load(response)
    bootstrap=call('/api/bootstrap');token=bootstrap['token']
    if not bootstrap.get('live_inference_ready'):raise RuntimeError('No installed local model connected')
    record=next(r for r in bootstrap['records'] if r['id']=='surgery')
    source='성명: 이도윤. 2024년 6월 12일 충수절제술 시행. 수술 3일 후에도 발열 없음. 수술 부위 감염 소견 없음.'
    submitted=call('/api/analyze',{'record_id':'custom-local-smoke','purpose_id':'record-default','purpose':record['purpose'],
        'custom_note':{'title':'새 합성 수술 후 경과기록','text':source,'synthetic_confirmed':True}})
    started=time.monotonic();last=None
    while time.monotonic()-started<1200:
        job=call('/api/jobs/'+submitted['job_id'])
        if job['state']!=last:print('Local live job: '+job['state'],flush=True);last=job['state']
        if job['state']=='done':break
        if job['state']=='error':raise RuntimeError(job.get('message'))
        time.sleep(3)
    else:raise TimeoutError('Local LLM test exceeded 20 minutes')
    view=job['view'];checks={
        'new_real_model_calls':view['llm']['calls']>0 and not view['llm']['cached'],
        'name_removed':'이도윤' not in view['output'],
        'surgery_name_date_preserved':'충수절제술' in view['output'] and '2024년 6월 12일' in view['output'],
        'elapsed_days_negation_preserved':'수술 3일 후에도 발열 없음' in view['output'] and '수술 부위 감염 소견 없음' in view['output'],
        'clinical_validation_eligible':view['validation']['eligible'],
        'no_residual_candidates':view['privacy']['residual_count']==0,
        'unregistered_source_release_held':not view['can_approve'] and view['collaboration']['status']=='HELD'}
    return {'scope':'One authored synthetic NEW note; local real inference, not a clinical benchmark.',
        'seconds':round(time.monotonic()-started,2),'checks':checks,'passed':all(checks.values()),'view':view}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--negative-control',action='store_true');args=parser.parse_args()
    result=negative_control() if args.negative_control else validate()
    target=Path('.runtime/local-live-negative-control.json' if args.negative_control else '.runtime/local-live-validation.json');target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='view'},ensure_ascii=False,indent=2),flush=True)
    raise SystemExit(0 if result['passed'] else 1)
