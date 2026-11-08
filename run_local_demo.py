"""Single loopback-only demo. Real Qwen prepared examples work offline.

New synthetic documents require an explicitly configured local Ollama model.
No automatic model download, external transfer, or shared-server connection.
"""
import os
for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[name]='1'
import argparse
from pathlib import Path
import sys
import tempfile
import json
from urllib.request import urlopen

from ClinicalCDSGateway.gateway.common import Denied
from ClinicalTextWeb.llm_client import ModelConfig,StructuredClient
from ClinicalTextWeb.prepared_cache import PreparedCache
from ClinicalTextWeb.service import MESSAGES
from demo_server import PublicDemo,Server,ROOT

MODEL_DIGEST='ad815644918f0eaab341c12b67837cc6dd4562342cdaf118f83d5d554cb37226'
MESSAGES['LOCAL_LIVE_UNAVAILABLE']='새 문서의 실시간 분석에는 로컬 모델 연결이 필요합니다. 현재는 준비된 수술 후 기록으로 전체 반출 절차를 시연할 수 있습니다.'
LOCAL_VERIFY_SCOPE='''
검증 범위: 원문에 대한 변환문의 충실도를 평가한다. 원문 자체의 의학적 진실성이나
그 결과가 임상적으로 좋은지 나쁜지는 평가하지 않는다. 원문의 모호함도 동일하게
유지되었다면 변환에 따른 의미 손상이 아니다. uncertainties에는 변환 때문에 발생한
의미 차이나 비교 근거의 부족을 구체적으로 적는다. 의학적 해설이나 추가 연구의 필요성을
문서 간 불일치로 보고하지 않는다. 동일한 인용 구절도 주변의 주체·부정·시간 연결이
달라졌다면 실패 또는 불확실로 판정한다. 실제 누락·추가·모순은 그대로 보고한다.
'''


def installed_local_model(model_url):
    """Discover only an existing local model; this does not load or download it."""
    try:
        with urlopen(model_url+'/api/tags',timeout=2) as response:
            models=json.loads(response.read(100000))
        return next((m['name'] for m in models.get('models',[]) if m.get('name')=='qwen3:4b-instruct'),None)
    except (OSError,ValueError,KeyError):return None


class LocalClient(StructuredClient):
    def __init__(self,config):
        super().__init__(config)
        self.keep_alive='60s'

    def complete(self,stage,instructions,payload,schema):
        if stage=='verify':instructions+='\n'+LOCAL_VERIFY_SCOPE
        return super().complete(stage,instructions,payload,schema)


def create_local_app(session_root,audit_root,live_model=None,model_url='http://127.0.0.1:11434'):
    app=PublicDemo(session_root,audit_directory=audit_root)
    cache=PreparedCache(ROOT/'prepared_examples',MODEL_DIGEST)
    app.local_mode=True;app.live_inference_ready=bool(live_model)
    app.service.llm.client_factory=LocalClient
    cached_config=app.service.local_model_config
    live_config={**cached_config,'base_url':model_url,'model':live_model,'verifier_model':live_model} if live_model else None
    def prepare(owner,data):
        prepared=data['record_id'] in app.collaboration.policy['project']['record_ids'] and not data.get('custom_note') and data['purpose']==app.records[data['record_id']]['purpose']
        if not prepared and not live_config:raise Denied('LOCAL_LIVE_UNAVAILABLE')
        target=cached_config if prepared else live_config
        desired=ModelConfig.parse(target).public()
        if app.service.llm.settings(owner)!=desired:app.service.llm.configure(owner,target)
    def provider(source,purpose,checks,phrases,config):
        if config.model=='qwen3:30b':
            try:return cache.get(source,purpose,checks,phrases,config)
            except Denied:
                if not live_model:raise Denied('LOCAL_LIVE_UNAVAILABLE')
        return None
    app.prepare_analysis=prepare;app.service.llm.prepared_provider=provider
    return app


def main():
    parser=argparse.ArgumentParser(description='새물길 외부 공동연구 제공 로컬 시연')
    parser.add_argument('--port',type=int,default=8773)
    parser.add_argument('--live-model',help='Installed local Ollama model for new synthetic notes; otherwise discover installed qwen3:4b-instruct')
    parser.add_argument('--offline',action='store_true',help='Prepared examples only; never run new inference')
    parser.add_argument('--model-url',default='http://127.0.0.1:11434')
    args=parser.parse_args()
    if args.model_url.rstrip('/') not in {'http://127.0.0.1:11434','http://localhost:11434'}:
        parser.error('Local demo accepts only the standard loopback Ollama endpoint.')
    live_model=None if args.offline else args.live_model or installed_local_model(args.model_url.rstrip('/'))
    runtime=ROOT/'.runtime/local-demo';runtime.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='session-',dir=runtime) as session_root:
        app=create_local_app(session_root,runtime/'audit',live_model,args.model_url)
        origin=f'http://127.0.0.1:{args.port}'
        try:server=Server(app,origin,args.port,local_config={'apiBase':'','publicUrl':origin,'githubUrl':'https://github.com/Hyunmin-3428/clinicaltext-emr'})
        except OSError:
            print(f'Port {args.port} is in use. Open {origin} or choose --port.',flush=True);return
        print(f'새물길 로컬 시연: {origin}',flush=True)
        print('실제 Qwen 사전 실행 결과 · 합성 기록 · 모의 역할 · 외부 전송 없음',flush=True)
        print('새 합성 문서: '+(live_model+' (로컬 추론)' if live_model else '모델 미연결 · 준비된 예제만 사용'),flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close();app.executor.shutdown(wait=True)


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf8')
    main()
