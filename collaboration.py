"""Synthetic external-collaboration release control. Actor selection is a demo,
not institutional authentication. No network transfer to a recipient is made.
"""
import base64
import copy
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
from functools import wraps
from contextlib import closing

from ClinicalCDSGateway.gateway.common import Denied,canonical,digest,exact_schema,strict_json
from ClinicalCDSGateway.gateway.engine import Gateway,POLICY,verify_external
from release_grading import REASONS

ROOT=Path(__file__).resolve().parent
CONTEXT_KEYS={'analysis_id','review_digest','project_id','recipient_id','actor_id'}
APPROVAL_KEYS=CONTEXT_KEYS|{'request_id','confirmed'}
OPEN_POLICY_ACTOR = "preapproved-o-policy"
MESSAGES={
    'COOP_ACTOR_DENIED':'현재 시연 역할로는 이 작업을 수행할 수 없습니다.',
    'COOP_SELF_APPROVAL':'처리·신청자가 자신의 요청을 승인할 수 없습니다.',
    'COOP_PROJECT_DENIED':'사전 승인된 공동연구 정보와 일치하지 않습니다.',
    'COOP_PURPOSE_DENIED':'이 기록·연구 목적은 첫 공동연구의 승인 범위에 포함되지 않습니다.',
    'COOP_RECIPIENT_DENIED':'선택한 환경은 이 공동연구의 승인된 수신처가 아니므로 반출을 차단했습니다.',
    'COOP_VALIDATION_HELD':'개인정보·임상정보 검사에 미충족 또는 불확실 항목이 있어 반출을 보류했습니다.',
    'COOP_APPROVAL_REQUIRED':'검토자의 승인 후에만 결과물을 생성할 수 있습니다.',
    'COOP_REVIEW_CHANGED':'문서·목적지·승인 조건이 변경되었거나 만료되어 다시 승인 요청해야 합니다.',
    'COOP_REVIEWER_HELD':'검토자가 반출을 보류했습니다.',
}


def public_policy():
    return json.loads((ROOT/'collaboration_policy.json').read_text(encoding='utf8'))


def audited(function):
    @wraps(function)
    def wrapped(self,owner,data):
        try:return function(self,owner,data)
        except Denied as error:
            with self.lock:
                entry=self.entries.get((owner,data.get('analysis_id'))) if isinstance(data,dict) else None
                if entry:
                    actor=next((a['id'] for a in self.policy['actors'] if a['id']==data.get('actor_id')),None)
                    self._audit(owner,'request_denied' if function.__name__=='request' else 'action_denied',entry,actor,error.code)
            raise
    return wrapped


class Collaboration:
    def __init__(self,root,service,records,*,policy=None,clock=time.time,audit_directory=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.service,self.records,self.clock=service,records,clock
        self.policy=copy.deepcopy(policy or public_policy());self.entries={};self.lock=threading.RLock()
        audit_root=Path(audit_directory) if audit_directory else self.root
        audit_root.mkdir(parents=True,exist_ok=True)
        self.audit_path=audit_root/'release-audit.sqlite'
        with closing(sqlite3.connect(self.audit_path)) as c:
            c.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,event BLOB NOT NULL,owner_hash TEXT NOT NULL)')
            c.commit()
        if os.name!='nt':self.root.chmod(0o700);audit_root.chmod(0o700);self.audit_path.chmod(0o600)
        self.gateway=None;self.tokens=None

    def _gateway(self):
        if self.gateway is None:
            # Existing gateway grades describe a synthetic transport fixture only.
            # Public decisions use this project's approval/recipient registry.
            policy=copy.deepcopy(POLICY);project=self.policy['project']
            policy.update(version=self.policy['version']+'-'+digest(canonical(self.policy))[:12],project=project['id'].lower(),release_ttl=self.policy['review_ttl_seconds'],
                          recipients={r['id']:{'grade':'O','local':True,'country':'KR','approved':True}
                                      for r in self.policy['recipients'] if r['approved'] and r['id'] in project['recipient_ids']})
            path=self.root/'gateway'
            Gateway.initialize(path,policy=policy)
            self.gateway=Gateway(path,adapter=self.service.llm.adapter,synthetic_validator=self.service.llm.registry)
            self.tokens={r:(self.gateway.internal/(r+'.token')).read_text(encoding='ascii') for r in ['submitter','reviewer','exporter']}
        return self.gateway

    def _audit(self,owner,event,entry,actor=None,code=None):
        item={'event':event,'time':int(self.clock()),'analysis_id':entry['view']['analysis_id'],
              'document_hash':digest(entry['view']['output']),'project_id':entry['project_id'],
              'recipient_id':entry['recipient_id'],'request_id':entry.get('request_id'),
              'actor_id':actor,'validation':self._validated(entry['view']),'reason':code,
              'release_grade':self._grade(entry)["grade"],
              'policy_version':self.policy['version'],'purpose_hash':digest(entry['view']['purpose']),
              'checks':{'privacy_residual':entry['view'].get('privacy',{}).get('residual_count'),
                        'clinical_passed':entry['view'].get('validation',{}).get('passed'),
                        'clinical_failed':entry['view'].get('validation',{}).get('failed')}}
        with closing(sqlite3.connect(self.audit_path)) as c:
            c.execute('INSERT INTO events(event,owner_hash) VALUES(?,?)',(canonical(item),digest(owner)))
            c.commit()

    def history(self,owner):
        self.service.session(owner)
        with closing(sqlite3.connect(self.audit_path)) as c:
            return [strict_json(r[0]) for r in c.execute('SELECT event FROM events WHERE owner_hash=? ORDER BY id DESC LIMIT 30',(digest(owner),))]

    @staticmethod
    def _validated(view):
        return view.get('can_approve') is True and view.get('validation',{}).get('eligible') is True and view.get('privacy',{}).get('residual_count')==0 and view.get('state')=='REVIEW_READY'

    @staticmethod
    def _binding(view):
        return digest(canonical({k:view.get(k) for k in ['analysis_id','review_digest','source','output','purpose','validation','privacy','can_approve','state']}))

    def _grade(self, entry):
        assessment = entry["assessment"]
        model_grade = assessment["model_grade"]
        approved_hash = self.policy["project"].get("open_outputs", {}).get(entry["record_id"])
        open_allowed = (
            model_grade == "O"
            and self._validated(entry["view"])
            and approved_hash == digest(entry["view"]["output"])
        )
        reason = REASONS[assessment["reason"]]
        if model_grade == "O" and not open_allowed:
            reason = "LLM은 O를 제안했지만 간소 반출 승인 범위에 해당하지 않아 S를 적용했습니다."
        return {
            "grade": "O" if open_allowed else "S",
            "model_grade": model_grade,
            "source": assessment["source"],
            "reason": reason,
        }

    def register(self,owner,view,record_id,*,assessment=None):
        with self.lock:
            self.entries[(owner,view['analysis_id'])]={'view':copy.deepcopy(view),'binding':self._binding(view),'record_id':record_id,
                'project_id':self.policy['project']['id'],'recipient_id':'university-b','status':'DRAFT',
                'assessment': copy.deepcopy(assessment) if assessment else {
                    "model_grade": None, "reason": "unavailable", "source": "fallback"
                },
                'request_id':None,'requested_by':None,'approved_by':None,'gateway_release':None,'artifact':None}
            return self._view(owner,self.entries[(owner,view['analysis_id'])])

    def _entry(self,owner,data):
        self.service.session(owner)
        entry=self.entries.get((owner,data.get('analysis_id')))
        if entry is None:raise Denied('WEB_NOT_FOUND')
        if data.get('review_digest')!=entry['view']['review_digest']:raise Denied('COOP_REVIEW_CHANGED')
        actual=self.service._entry(owner,data['analysis_id'])['view']
        if self._binding(actual)!=entry['binding']:
            self._invalidate(entry);self._audit(owner,'invalidated',entry,code='COOP_REVIEW_CHANGED')
            raise Denied('COOP_REVIEW_CHANGED')
        return entry

    def _actor(self,data,role=None):
        actor=next((a for a in self.policy['actors'] if a['id']==data.get('actor_id')),None)
        if actor is None or (role and actor['role']!=role):raise Denied('COOP_ACTOR_DENIED')
        return actor['id']

    def _invalidate(self,entry):
        if entry.get('gateway_release') and entry['status'] in {'REQUESTED','APPROVED'}:
            self.gateway.revoke(entry['gateway_release'],self.tokens['reviewer'])
        entry.update(status='DRAFT',request_id=None,requested_by=None,approved_by=None,gateway_release=None,artifact=None)

    def _condition(self,entry):
        project=self.policy['project']
        if not self._validated(entry['view']):return 'COOP_VALIDATION_HELD'
        if project['approved'] is not True or entry['project_id']!=project['id']:return 'COOP_PROJECT_DENIED'
        record=self.records.get(entry['record_id'])
        if entry['record_id'] not in project['record_ids'] or not record or entry['view']['purpose']!=record['purpose']:return 'COOP_PURPOSE_DENIED'
        recipient=next((r for r in self.policy['recipients'] if r['id']==entry['recipient_id']),None)
        if not recipient or not recipient['approved'] or recipient['id'] not in project['recipient_ids']:return 'COOP_RECIPIENT_DENIED'
        return None

    def _view(self,owner,entry):
        view=copy.deepcopy(entry['view']);code=self._condition(entry)
        view['collaboration']={k:entry[k] for k in ['project_id','recipient_id','status','request_id','requested_by','approved_by']}
        view['collaboration'].update(simulation=True,policy_version=self.policy['version'],condition=code,
            message=MESSAGES.get(code,'승인된 공동연구 환경입니다. 검토자 승인 후 파일을 생성할 수 있습니다.'))
        view['collaboration']['release_grade'] = self._grade(entry)
        if code:view['collaboration']['status']='HELD' if code=='COOP_VALIDATION_HELD' else 'BLOCKED'
        view['release_history']=self.history(owner)
        return view

    def status(self,owner,data):
        exact_schema(data,{'analysis_id','review_digest'})
        with self.lock:return self._view(owner,self._entry(owner,data))

    def _context(self,entry,data):
        if (entry['project_id'],entry['recipient_id'])!=(data['project_id'],data['recipient_id']):
            self._invalidate(entry);entry.update(project_id=data['project_id'],recipient_id=data['recipient_id'])

    @audited
    def select(self,owner,data):
        exact_schema(data,CONTEXT_KEYS)
        with self.lock:
            entry=self._entry(owner,data);actor=self._actor(data,'requester');self._context(entry,data)
            code=self._condition(entry)
            entry['status']='BLOCKED' if code and code!='COOP_VALIDATION_HELD' else 'HELD' if code else entry['status']
            self._audit(owner,'destination_checked',entry,actor,code)
            return self._view(owner,entry)

    @audited
    def request(self,owner,data):
        exact_schema(data,CONTEXT_KEYS)
        with self.lock:
            entry=self._entry(owner,data);actor=self._actor(data,'requester');self._context(entry,data)
            code=self._condition(entry)
            if code:
                entry['status']='HELD' if code=='COOP_VALIDATION_HELD' else 'BLOCKED'
                raise Denied(code)
            if (entry["status"] == "REQUESTED"
                and entry["policy_hash"] == digest(canonical(self.policy))
                and self.clock() <= entry["requested_at"] + self.policy["review_ttl_seconds"]):
                if self._grade(entry)["grade"] == "O":
                    self._approve_entry(owner, entry, OPEN_POLICY_ACTOR, "policy_approved")
                return self._view(owner,entry)
            self._invalidate(entry)
            gateway=self._gateway()
            from ClinicalCDSGateway.gateway.fixtures import request
            with self.service.lock:
                evidence=self.service._entry(owner,data['analysis_id'])['evidence'];document='coop-'+uuid.uuid4().hex
                self.service.llm.registry.entries[document]=evidence
                self.service.llm.adapter.prepared=evidence
                try:result=gateway.submit(request(text=entry['view']['source'],document=document,project=entry['project_id'].lower(),recipient=entry['recipient_id']),self.tokens['submitter'])
                finally:self.service.llm.adapter.prepared=None
            if result['state']!='REVIEW':raise Denied('COOP_VALIDATION_HELD')
            entry.update(status='REQUESTED',request_id=secrets.token_urlsafe(18),requested_by=actor,
                         gateway_release=result['release'],policy_hash=digest(canonical(self.policy)),requested_at=self.clock())
            self._audit(owner,'requested',entry,actor)
            if self._grade(entry)["grade"] == "O":
                self._approve_entry(owner, entry, OPEN_POLICY_ACTOR, "policy_approved")
            return self._view(owner,entry)

    def _review(self,owner,data,required_status):
        entry=self._entry(owner,data)
        if (entry['project_id'],entry['recipient_id'])!=(data['project_id'],data['recipient_id']):
            self._context(entry,data);self._audit(owner,'invalidated',entry,data['actor_id'],'COOP_REVIEW_CHANGED');raise Denied('COOP_REVIEW_CHANGED')
        code=self._condition(entry)
        if code:raise Denied(code)
        if entry['status'] not in required_status or not data['request_id'] or data['request_id']!=entry['request_id'] or data['confirmed'] is not True:raise Denied('COOP_APPROVAL_REQUIRED')
        if entry.get('policy_hash')!=digest(canonical(self.policy)) or self.clock()>entry['requested_at']+self.policy['review_ttl_seconds']:
            self._invalidate(entry);self._audit(owner,'invalidated',entry,data['actor_id'],'COOP_REVIEW_CHANGED');raise Denied('COOP_REVIEW_CHANGED')
        return entry

    def _approve_entry(self, owner, entry, actor, event):
        self.gateway.approve(entry["gateway_release"], self.tokens["reviewer"], digest(entry["view"]["output"]))
        entry.update(status="APPROVED", approved_by=actor)
        self._audit(owner, event, entry, actor)

    @audited
    def approve(self,owner,data):
        exact_schema(data,APPROVAL_KEYS)
        with self.lock:
            actor=self._actor(data,'reviewer');entry=self._review(owner,data,{'REQUESTED'})
            if actor==entry['requested_by']:raise Denied('COOP_SELF_APPROVAL')
            self._approve_entry(owner, entry, actor, "approved")
            return self._view(owner,entry)

    @audited
    def hold(self,owner,data):
        exact_schema(data,APPROVAL_KEYS)
        with self.lock:
            actor=self._actor(data,'reviewer');entry=self._review(owner,data,{'REQUESTED','APPROVED'})
            self._invalidate(entry);entry['status']='HELD';self._audit(owner,'reviewer_held',entry,actor,'COOP_REVIEWER_HELD')
            return self._view(owner,entry)

    @audited
    def export(self,owner,data):
        exact_schema(data,APPROVAL_KEYS)
        with self.lock:
            actor=self._actor(data);entry=self._review(owner,data,{'APPROVED','EXPORTED'})
            if entry['artifact'] is None:
                result=self.gateway.export(entry['gateway_release'],self.tokens['exporter']);raw=Path(result['file']).read_bytes()
                entry['artifact']={'filename':'seamul-'+entry['gateway_release'][:8]+'.json','artifact_b64':base64.b64encode(raw).decode(),
                    'verification_key_b64':base64.b64encode((self.gateway.external/'verification.key').read_bytes()).decode()}
            artifact=entry['artifact']
            verify_external(strict_json(base64.b64decode(artifact['artifact_b64'])),base64.b64decode(artifact['verification_key_b64']),expected_recipient=entry['recipient_id'])
            entry['status']='EXPORTED';self._audit(owner,'file_generated',entry,actor)
            receipt={'simulation':True,'transfer_performed':False,'project':self.policy['project'],'recipient_id':entry['recipient_id'],
                'requested_by':entry['requested_by'],'approved_by':entry['approved_by'],'request_id':entry['request_id'],
                'document_hash':digest(entry['view']['output']),'policy_version':self.policy['version'],'validation_passed':True,
                'release_grade':self._grade(entry)["grade"]}
            return {**artifact,'view':self._view(owner,entry),'receipt_b64':base64.b64encode(canonical(receipt)).decode()}
