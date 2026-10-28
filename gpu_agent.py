"""Cache-first presentation agent with separate light/control and GPU lanes."""
from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('MKL_NUM_THREADS','1')
import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.request import Request,urlopen
from ClinicalCDSGateway.gateway.common import Denied
from ClinicalTextWeb.service import MESSAGES
from ClinicalTextWeb.prepared_cache import PreparedCache,cached_cases
from ClinicalTextWeb.resource_guard import ResourceGuard,GuardedClient,LIMITS
from demo_server import PublicDemo
from warm_event_cache import model_digest

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--site',required=True);parser.add_argument('--token-file',required=True)
    parser.add_argument('--model-url',default='http://127.0.0.1:11435');parser.add_argument('--model',default='qwen3:30b');parser.add_argument('--runtime',required=True)
    args=parser.parse_args()
    if not args.site.startswith('https://'):parser.error('HTTPS site required')
    try:os.nice(10)
    except OSError:pass
    token=Path(args.token_file).read_text().strip()
    runtime=Path(args.runtime);runtime.mkdir(parents=True,exist_ok=True)
    def post(path,data):
        request=Request(args.site.rstrip('/')+path,data=json.dumps(data,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'ClinicalText-GPU-agent'})
        with urlopen(request,timeout=25) as response:return json.load(response)
    guard=ResourceGuard(runtime,args.model_url,args.model)
    cache=PreparedCache(runtime/'prepared-cache',model_digest(args.model_url,args.model))
    cases=cached_cases()
    with tempfile.TemporaryDirectory(prefix='sessions-',dir=runtime) as directory:
        app=PublicDemo(directory,args.model_url,args.model)
        local=threading.local();sessions={};session_lock=threading.Lock()
        def provider(source,purpose,checks,phrases,config):
            if getattr(local,'cached',False):return cache.get(source,purpose,checks,phrases,config)
            return None
        app.service.llm.prepared_provider=provider
        app.service.llm.client_factory=lambda config:GuardedClient(config,guard)
        def heartbeat():
            while True:
                try:
                    ready=[]
                    for case in cases:
                        from ClinicalTextWeb.llm_client import ModelConfig
                        cfg=ModelConfig('ollama',args.model_url,args.model,args.model)
                        # Fingerprint/key presence is cheap; get() revalidates each
                        # actual cache use before a new approval record is issued.
                        if (cache.directory/(cache.key(case['text'],case['purpose'],case['checks'],[],cfg)+'.json')).is_file():ready.append({'record_id':case['id'],'purpose':case['purpose']})
                    post('/internal/heartbeat',{'cache_ready':ready,'live_ready':guard.ready(),'llm_budget_remaining_seconds':round(guard.remaining()),'limits_version':LIMITS['version']})
                except Exception:pass
                time.sleep(10)
        def work(lane):
            pending=None
            while True:
                try:
                    if pending:
                        post('/internal/complete',pending)
                        (runtime/('last-result-'+lane+'.json')).write_text(json.dumps({'job_id':pending['id'],'ok':pending['ok'],'finished':time.time()}))
                        pending=None
                    job=post('/internal/claim',{'lane':lane})['job']
                    if not job:time.sleep(2);continue
                    with session_lock:
                        if job['owner'] not in sessions:sessions[job['owner']]=app.service.new_session()[0]
                        identifier=sessions[job['owner']]
                    try:
                        data=job['payload'];local.cached=job.get('execution')=='cached'
                        if job['kind']=='analyze' and 'custom_note' not in data and any(c['id']==data.get('record_id') and c['purpose']==data.get('purpose') for c in cases):local.cached=True
                        if job['kind']=='analyze':
                            if 'custom_note' in data:
                                custom=data['custom_note']
                                if custom.get('synthetic_confirmed') is not True or not isinstance(custom.get('text'),str) or len(custom['text'])>LIMITS['live_max_chars']:raise Denied('WEB_INPUT_INVALID')
                                from ClinicalTextWeb.input_text import normalize_text
                                normalize_text(custom['text'])
                                record={'text':custom['text'],'checks':['treatment','measurements','time','negation','family']}
                            else:record=app.records[data['record_id']]
                            inputs={'engine':'llm','purpose':data['purpose'],'text':record['text'],'checks':record['checks'],'required_phrases':[],'synthetic_consent':True}
                            if local.cached:result=app.service.analyze(identifier,inputs)
                            else:
                                with guard.lease():result=app.service.analyze(identifier,inputs)
                        elif job['kind']=='approve':result=app.approve(identifier,data)
                        elif job['kind']=='export':result=app.export(identifier,data)
                        else:raise Denied('WEB_INPUT_INVALID')
                        pending={'id':job['id'],'ok':True,'result':result}
                    except Exception as error:
                        code=error.code if isinstance(error,Denied) else 'WEB_INTERNAL_ERROR'
                        messages={'DEMO_CACHE_NOT_READY':'예제 실행 결과를 준비하고 있습니다. 잠시 후 다시 시도해 주세요.','DEMO_GPU_BUSY':'다른 서버 작업을 보호하기 위해 실시간 분석을 잠시 중지했습니다. 기본 예제를 이용해 주세요.','DEMO_LIVE_TIME_LIMIT':'실시간 처리 시간 한도에 도달했습니다. 기본 예제를 이용해 주세요.','DEMO_GPU_BUDGET':'시간당 GPU 사용 한도에 도달했습니다. 기본 예제를 이용해 주세요.'}
                        pending={'id':job['id'],'ok':False,'result':{'message':messages.get(code,MESSAGES.get(code,'처리를 완료하지 못했습니다. 다시 실행해 주세요.')),'error':code}}
                except Exception as error:
                    print('Connection waiting '+lane+': '+type(error).__name__,flush=True);time.sleep(5)
        threading.Thread(target=heartbeat,daemon=True).start()
        threading.Thread(target=work,args=('fast',),daemon=True).start()
        print('Presentation agent: cached/control lane + single guarded GPU lane.',flush=True)
        work('live')

if __name__=='__main__':main()
