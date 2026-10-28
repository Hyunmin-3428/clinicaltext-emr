"""Cross-note preservation and real detector behavior on authored synthetic text."""
import json
from pathlib import Path
import unittest
from build_catalog import catalog
from ClinicalTextWeb.synthetic_notes import neonatal_examples
from ClinicalTextWeb.llm_service import AUTHORED_SOURCES
from ClinicalTextWeb.clinical import verify_relations
from ClinicalTextDeid.detectors import detect
from ClinicalCDSGateway.gateway.common import digest
from ClinicalCDSGateway.gateway.detector import residual_check

class SyntheticChartTests(unittest.TestCase):
    def test_public_catalog_and_agent_use_identical_authored_records(self):
        expected=catalog()
        actual=json.loads((Path(__file__).resolve().parents[1]/'catalog.json').read_text(encoding='utf8'))
        self.assertEqual(actual,expected)
        self.assertEqual(len(actual['records']),7)
        for r in neonatal_examples():
            self.assertIn(digest(r['text']),AUTHORED_SOURCES)
            self.assertLessEqual(len(r['text']),1000)
    def test_chart_keeps_pending_and_final_results_in_their_own_notes(self):
        admission,progress,discharge=neonatal_examples()
        for r in [admission,progress]:
            self.assertIn('미확인',r['text']);self.assertIn('rule out',r['text'])
            self.assertNotIn('48시간 음성',r['text'])
        self.assertIn('48시간 음성',discharge['text'])
        self.assertIn('입원 3일 경과',discharge['text'])
    def test_clinical_signs_and_generic_heading_are_not_identifiers(self):
        for r in neonatal_examples():
            candidates=detect(r['text'])
            people=[r['text'][v['start']:v['end']] for v in candidates if v['type']=='PERSON']
            self.assertEqual(people,['윤가람'])
            self.assertFalse(any(r['text'][v['start']:v['end']]=='Hospital' for v in candidates))
        named='성명: 빈호흡. Riverbank Hospital에서 내원.'
        candidates=detect(named)
        self.assertTrue(any(named[v['start']:v['end']]=='빈호흡' and v['type']=='PERSON' for v in candidates))
        self.assertTrue(any('Riverbank Hospital' in named[v['start']:v['end']] for v in candidates))
    def test_clinical_sign_damage_is_detected(self):
        r=neonatal_examples()[0]
        damaged=r['text'].replace('빈호흡','<PERSON_2>')
        result=verify_relations(r['text'],damaged,r['checks'],r['purpose'],[])
        self.assertFalse(result['eligible'])
        self.assertTrue(any(v['kind']=='clinical_sign' and v['status']=='failed' for v in result['rows']))

    def test_generated_fallback_id_field_is_not_an_unmasked_identifier(self):
        source='병록번호: SIM-N3401'
        self.assertNotIn('RESIDUAL_ID',residual_check(source,'병록번호: <MRN_AUTO_2>'))
        self.assertNotIn('RESIDUAL_ID',residual_check(source,'병록번호: <ID_1>'))
        self.assertIn('RESIDUAL_ID',residual_check(source,'병록번호: SIM-N3401'))
        self.assertIn('RESIDUAL_ID',residual_check(source,'병록번호: <MRN_AUTO_2_SIM-N3401>'))
        self.assertIn('RESIDUAL_ID',residual_check('병록번호: <MRN_AUTO_2>','병록번호: <MRN_AUTO_2>'))
        self.assertIn('RESIDUAL_ID',residual_check(source,'병록번호: <MRN_AUTO_2>\nMRN: OTHER-4321'))

if __name__=='__main__':unittest.main()
