import json
import tempfile
import unittest
from ClinicalCDSGateway.gateway.common import Denied
from ClinicalTextWeb.llm_client import ModelConfig
from ClinicalTextWeb.llm_pipeline import apply_edits,audit_evidence
from ClinicalTextWeb.prepared_cache import PreparedCache
from ClinicalTextWeb.temporal_policy import POLICY_VERSION
from ClinicalTextWeb.tests.test_llm import plan,audit
from ClinicalTextWeb.llm_service import SURGERY

class PreparedCacheTests(unittest.TestCase):
    def setUp(self):
        self.cfg=ModelConfig('ollama','http://127.0.0.1:11435','test-model','test-model')
        self.purpose='수술 후 감염 연구';self.checks=['treatment','time','negation']
        proposal=plan();output,edits=apply_edits(SURGERY,proposal,[])
        validation=audit_evidence(SURGERY,output,self.purpose,self.checks,[],proposal,audit(),applied_edits=edits)
        self.prepared={'output':output,'edits':edits,'validation':validation,'bundle':{'policy_version':POLICY_VERSION,'plan':proposal,'audit':audit(),'applied_edits':edits},'attempts':1,'calls':3,'metrics':[]}
    def test_cached_model_result_is_revalidated_and_reports_zero_new_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            c=PreparedCache(folder,'digest-1');c.put(SURGERY,self.purpose,self.checks,[],self.cfg,self.prepared)
            result=c.get(SURGERY,self.purpose,self.checks,[],self.cfg)
            self.assertTrue(result['cached']);self.assertEqual(result['calls'],0)
            self.assertEqual(result['original_calls'],3);self.assertEqual(result['output'],self.prepared['output'])
    def test_changed_model_or_purpose_cannot_reuse_cached_result(self):
        with tempfile.TemporaryDirectory() as folder:
            c=PreparedCache(folder,'digest-1');c.put(SURGERY,self.purpose,self.checks,[],self.cfg,self.prepared)
            for other,purpose in [(PreparedCache(folder,'digest-2'),self.purpose),(c,'다른 연구 목적')]:
                with self.assertRaises(Denied):other.get(SURGERY,purpose,self.checks,[],self.cfg)
    def test_tampered_clinical_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            c=PreparedCache(folder,'digest-1');c.put(SURGERY,self.purpose,self.checks,[],self.cfg,self.prepared)
            path=c.directory/(c.key(SURGERY,self.purpose,self.checks,[],self.cfg)+'.json')
            value=json.loads(path.read_text(encoding="utf8"));value['prepared']['output']=value['prepared']['output'].replace('발열 없음','발열 있음');path.write_text(json.dumps(value))
            with self.assertRaises(Denied):c.get(SURGERY,self.purpose,self.checks,[],self.cfg)

if __name__=='__main__':unittest.main()
