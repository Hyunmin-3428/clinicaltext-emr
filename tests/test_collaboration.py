"""Real gateway/SQLite integration with explicitly simulated LLM responses."""
import base64
import copy
from pathlib import Path
import tempfile
import unittest

from ClinicalCDSGateway.gateway.common import Denied,digest,strict_json
from ClinicalCDSGateway.gateway.engine import verify_external
from ClinicalTextWeb.service import DemoService
from ClinicalTextWeb.tests.test_llm import SimulatedClient
from demo_server import PublicDemo


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        cfg={'provider':'ollama','base_url':'http://127.0.0.1:9','model':'simulated','verifier_model':'simulated','api_key':''}
        service=DemoService(root/'core',local_model_config=cfg)
        service.llm.client_factory=SimulatedClient
        self.app=PublicDemo(root/'app',service=service,audit_directory=root/'audit')
        self.service=service;self.token=self.app.bootstrap()['token']
        record=self.app.records['surgery']
        view=service.analyze(self.token,{'engine':'llm','purpose':record['purpose'],'text':record['text'],'checks':record['checks'],'required_phrases':[],'synthetic_consent':True})
        self.assertTrue(view['can_approve'],view['reasons'])
        self.view=self.app.collaboration.register(self.token,view,'surgery')
        self.data={'analysis_id':view['analysis_id'],'review_digest':view['review_digest'],'project_id':'POSTOP-COOP-001','recipient_id':'university-b','actor_id':'data-officer-a'}

    def tearDown(self):self.app.executor.shutdown(wait=True);self.tmp.cleanup()

    def request(self):
        return self.app.request_release(self.token,self.data)

    def review_data(self,view,**changes):
        return {**self.data,'request_id':view['collaboration']['request_id'],'confirmed':True,'actor_id':'reviewer-b',**changes}

    def approve(self):
        requested=self.request();data=self.review_data(requested)
        self.app.approve(self.token,data);return data

    def test_external_approved_recipient_exports_signed_correct_recipient(self):
        data=self.approve();result=self.app.export(self.token,data)
        manifest=verify_external(strict_json(base64.b64decode(result['artifact_b64'])),base64.b64decode(result['verification_key_b64']),expected_recipient='university-b')
        self.assertEqual(manifest['recipient'],'university-b')
        receipt=strict_json(base64.b64decode(result['receipt_b64']))
        self.assertEqual(receipt['requested_by'],'data-officer-a');self.assertEqual(receipt['approved_by'],'reviewer-b')
        self.assertFalse(receipt['transfer_performed']);self.assertIn('3월 10일',base64.b64decode(result['text_b64']).decode('utf-8-sig'))
        self.assertIn('수술 3일 후에도 발열 없음',result['view']['output'])

    def test_open_grade_releases_under_policy_and_still_blocks_other_destinations(self):
        self.app.collaboration.policy["project"]["open_outputs"] = {"surgery": digest(self.view["output"])}
        self.app.collaboration.register(self.token, self.view, "surgery", assessment={
            "model_grade": "O", "reason": "limited_context", "source": "prepared"
        })
        requested = self.request()
        self.assertEqual(requested["collaboration"]["status"], "APPROVED")
        data = self.review_data(requested, actor_id="data-officer-a")
        result = self.app.export(self.token, data)
        receipt = strict_json(base64.b64decode(result["receipt_b64"]))
        self.assertEqual(receipt["release_grade"], "O")
        self.assertEqual(receipt["approved_by"], "preapproved-o-policy")
        blocked = self.app.select_destination(self.token, {**self.data, "recipient_id": "unregistered-cloud"})
        self.assertEqual(blocked["collaboration"]["status"], "BLOCKED")
        with self.assertRaises(Denied):
            self.app.export(self.token, data)

    def test_model_open_recommendation_requires_the_preapproved_output(self):
        self.app.collaboration.policy["project"]["open_outputs"] = {"surgery": "wrong-document-hash"}
        view = self.app.collaboration.register(self.token, self.view, "surgery", assessment={
            "model_grade": "O", "reason": "limited_context", "source": "live"
        })
        self.assertEqual(view["collaboration"]["release_grade"]["grade"], "S")
        self.assertEqual(self.request()["collaboration"]["status"], "REQUESTED")

    def test_direct_old_approve_or_export_cannot_skip_selection(self):
        old={k:self.data[k] for k in ['analysis_id','review_digest']}|{'confirmed':True}
        for action in [self.app.approve,self.app.export]:
            with self.assertRaisesRegex(Denied,'SCHEMA_DENIED'):action(self.token,old)
        with self.assertRaisesRegex(Denied,'COOP_APPROVAL_REQUIRED'):self.app.export(self.token,self.review_data(self.view))

    def test_unapproved_and_unknown_recipient_rejected_on_server(self):
        for recipient in ['unregistered-cloud','forged-university']:
            with self.assertRaisesRegex(Denied,'COOP_RECIPIENT_DENIED'):self.app.request_release(self.token,{**self.data,'recipient_id':recipient})
        self.assertTrue(any(e['reason']=='COOP_RECIPIENT_DENIED' for e in self.app.collaboration.history(self.token)))

    def test_unknown_project_and_unapproved_research_rejected(self):
        with self.assertRaisesRegex(Denied,'COOP_PROJECT_DENIED'):self.app.request_release(self.token,{**self.data,'project_id':'unapproved'})
        self.app.collaboration.policy['project']['approved']=False
        with self.assertRaisesRegex(Denied,'COOP_PROJECT_DENIED'):self.request()

    def test_requester_cannot_approve_and_reviewer_cannot_submit(self):
        requested=self.request()
        with self.assertRaisesRegex(Denied,'COOP_ACTOR_DENIED'):self.app.approve(self.token,self.review_data(requested,actor_id='data-officer-a'))
        with self.assertRaisesRegex(Denied,'COOP_ACTOR_DENIED'):self.app.request_release(self.token,{**self.data,'actor_id':'reviewer-b'})

    def test_destination_change_after_approval_revokes_previous_download(self):
        old=self.approve()
        selected=self.app.select_destination(self.token,{**self.data,'recipient_id':'unregistered-cloud'})
        self.assertEqual(selected['collaboration']['status'],'BLOCKED')
        with self.assertRaises(Denied):self.app.export(self.token,old)
        selected=self.app.select_destination(self.token,self.data)
        self.assertIsNone(selected['collaboration']['request_id'])
        with self.assertRaises(Denied):self.app.export(self.token,old)

    def test_content_change_after_approval_blocks_export(self):
        data=self.approve();entry=self.service._entry(self.token,self.data['analysis_id'])
        entry['view']['output']=entry['view']['output'].replace('발열 없음','발열 있음')
        with self.assertRaisesRegex(Denied,'COOP_REVIEW_CHANGED'):self.app.export(self.token,data)

    def test_policy_change_after_approval_requires_new_review(self):
        data=self.approve();self.app.collaboration.policy['version']='changed-policy'
        with self.assertRaisesRegex(Denied,'COOP_REVIEW_CHANGED'):self.app.export(self.token,data)

    def test_approval_expiry_blocks_export(self):
        data=self.approve();entry=self.app.collaboration.entries[(self.token,self.data['analysis_id'])]
        self.app.collaboration.clock=lambda:entry['requested_at']+601
        with self.assertRaisesRegex(Denied,'COOP_REVIEW_CHANGED'):self.app.export(self.token,data)

    def test_other_research_purpose_cannot_use_the_fixed_approval(self):
        entry=self.app.collaboration.entries[(self.token,self.data['analysis_id'])]
        entry['view']['purpose']='다른 연구 목적';entry['binding']=self.app.collaboration._binding(entry['view'])
        self.service._entry(self.token,self.data['analysis_id'])['view']['purpose']='다른 연구 목적'
        with self.assertRaisesRegex(Denied,'COOP_PURPOSE_DENIED'):self.request()

    def test_failed_or_uncertain_validation_cannot_be_overridden(self):
        for recipient in ['university-b','unregistered-cloud']:
            entry=self.app.collaboration.entries[(self.token,self.data['analysis_id'])]
            entry['view']['can_approve']=False;entry['binding']=self.app.collaboration._binding(entry['view'])
            self.service._entry(self.token,self.data['analysis_id'])['view']['can_approve']=False
            with self.assertRaisesRegex(Denied,'COOP_VALIDATION_HELD'):self.app.request_release(self.token,{**self.data,'recipient_id':recipient})

    def test_reviewer_hold_prevents_file_generation(self):
        requested=self.request();data=self.review_data(requested)
        held=self.app.hold_release(self.token,data)
        self.assertEqual(held['collaboration']['status'],'HELD')
        with self.assertRaises(Denied):self.app.export(self.token,data)

    def test_other_session_cannot_read_or_approve_release(self):
        data=self.approve();other=self.app.bootstrap()['token']
        with self.assertRaisesRegex(Denied,'WEB_NOT_FOUND'):self.app.export(other,data)
        self.assertEqual(self.app.collaboration.history(other),[])

    def test_repeated_formats_recheck_binding_without_second_gateway_release(self):
        data=self.approve();one=self.app.export(self.token,data);two=self.app.export(self.token,data)
        self.assertEqual(one['artifact_b64'],two['artifact_b64']);self.assertTrue(two['xlsx_b64'])
        self.app.select_destination(self.token,{**self.data,'recipient_id':'unregistered-cloud'})
        with self.assertRaises(Denied):self.app.export(self.token,data)

    def test_audit_is_persistent_and_has_no_raw_notes_or_bearer_tokens(self):
        data=self.approve();self.app.export(self.token,data)
        log=self.app.collaboration.history(self.token)
        self.assertTrue({'requested','approved','file_generated'}<={e['event'] for e in log})
        raw=self.app.collaboration.audit_path.read_bytes()
        self.assertNotIn(self.token.encode(),raw);self.assertNotIn('김민수'.encode(),raw)
        self.assertTrue(self.app.collaboration.audit_path.parent.name=='audit')


if __name__=='__main__':unittest.main()
