"""Exercise the running local demo with real prepared model results; no inference."""
import base64
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from ClinicalCDSGateway.gateway.common import strict_json
from ClinicalCDSGateway.gateway.engine import verify_external


def validate(origin='http://127.0.0.1:8773'):
    token='';checks=[]
    def call(path,data=None):
        headers={'Authorization':'Bearer '+token} if token else {}
        if data is not None:headers['Content-Type']='application/json'
        req=Request(origin+path,data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None,headers=headers)
        try:
            with urlopen(req,timeout=30) as r:return json.load(r)
        except HTTPError as e:
            detail=json.load(e);raise RuntimeError(detail['error']) from None
    def check(name,condition):
        checks.append({'name':name,'passed':bool(condition)})
        if not condition:raise AssertionError(name)
    results=[]
    for record_id in ['surrogate','surgery']:
        boot=call('/api/bootstrap');token=boot['token'];record=next(r for r in boot['records'] if r['id']==record_id)
        submitted=call('/api/analyze',{'record_id':record_id,'purpose_id':'record-default','purpose':record['purpose']})
        deadline=time.monotonic()+60
        while True:
            job=call('/api/jobs/'+submitted['job_id'])
            if job['state']=='done':view=job['view'];break
            if job['state']=='error':raise RuntimeError(job.get('message'))
            if time.monotonic()>deadline:raise TimeoutError('local analysis')
            time.sleep(.1)
        check(record_id+'_real_prepared_result_zero_new_llm_calls',view['llm']['cached'] and view['llm']['calls']==0)
        check(record_id+'_dual_validation_passed',view['validation']['eligible'] and view['privacy']['residual_count']==0)
        check(record_id+'_elapsed_time_and_negation_preserved','수술 3일 후에도 발열' in view['output'] and ('없음' in view['output'] or '없었고' in view['output']))
        check(record_id+'_actual_surgery_date_preserved',('2023년 3월 10일' if record_id=='surrogate' else '3월 10일') in view['output'])
        data={'analysis_id':view['analysis_id'],'review_digest':view['review_digest'],'project_id':boot['collaboration']['project']['id'],'recipient_id':'university-b','actor_id':'data-officer-a'}
        requested=call('/api/release/request',data)
        grade = view['collaboration']['release_grade']['grade']
        review={**data,'request_id':requested['collaboration']['request_id'],'confirmed':True}
        if record_id == 'surgery':
            check(record_id+'_open_policy_approved', grade == 'O' and requested['collaboration']['status'] == 'APPROVED'
                  and requested['collaboration']['approved_by'] == 'preapproved-o-policy')
        else:
            check(record_id+'_sensitive_requires_review', grade == 'S' and requested['collaboration']['status'] == 'REQUESTED')
            try:call('/api/approve',review)
            except RuntimeError as e:check(record_id+'_self_approval_blocked',str(e)=='COOP_ACTOR_DENIED')
            else:check(record_id+'_self_approval_blocked',False)
            review['actor_id'] = 'reviewer-b'
            approved=call('/api/approve',review);check(record_id+'_reviewer_approved',approved['collaboration']['status']=='APPROVED')
        artifact=call('/api/export',review)
        manifest=verify_external(strict_json(base64.b64decode(artifact['artifact_b64'])),base64.b64decode(artifact['verification_key_b64']),expected_recipient='university-b')
        check(record_id+'_signed_correct_recipient',manifest['recipient']=='university-b')
        receipt=strict_json(base64.b64decode(artifact['receipt_b64']))
        check(record_id+'_file_only_no_external_transfer',receipt['transfer_performed'] is False and receipt['requested_by']!=receipt['approved_by']
              and receipt['release_grade']==grade)
        blocked=call('/api/release/select',{**data,'recipient_id':'unregistered-cloud'})
        check(record_id+'_unapproved_destination_blocked',blocked['collaboration']['status']=='BLOCKED')
        try:call('/api/export',review)
        except RuntimeError:check(record_id+'_old_approval_cannot_be_reused',True)
        else:check(record_id+'_old_approval_cannot_be_reused',False)
        results.append({'record_id':record_id,'original_model_calls':view['llm'].get('original_calls'),'clinical_checks':view['validation']['passed']})
    return {'scope':'Authored synthetic records, real prior Qwen runs, local policy integration; not clinical performance certification.',
            'new_llm_calls':0,'records':results,'checks':checks,'passed':all(c['passed'] for c in checks)}


if __name__=='__main__':
    result=validate();target=Path('.runtime/local-collaboration-validation.json');target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
