import copy
import unittest
from ClinicalCDSGateway.gateway.common import Denied
from ClinicalTextDeid.clinical_guards import protected_spans
from ClinicalTextWeb.clinical import verify_relations
from ClinicalTextWeb.llm_pipeline import apply_edits

# Independently authored synthetic example; not copied from a patient screenshot.
SOURCE='성명: 한도윤.\nIUP 38+5주, 출생체중 2.95 kg, 남아.\nNSVD로 출생. 생후 약 25분 기관삽관 후 NICU 전원.'
def proposal(edits):
    return {'conditions':[{'id':'c','relation':'분만 방법','source_quote':'NSVD로 출생'}],'diagnoses':[],'edits':edits,'uncertainties':[]}

class ClinicalEditTests(unittest.TestCase):
    def test_expansion_is_rejected_even_when_ner_calls_it_an_organization(self):
        for quote,replacement in [('IUP 38+5주','임신 38주+5일'),('NSVD','자연분만')]:
            start=SOURCE.index(quote)
            candidates=[{'start':start,'end':start+len(quote),'type':'ORGANIZATION','method':'simulated_false_positive'}]
            p=proposal([{'quote':quote,'occurrence':0,'type':'QUASI','replacement':replacement,'reason':'표현 개선'}])
            with self.subTest(quote=quote):
                with self.assertRaises(Denied) as error:apply_edits(SOURCE,p,candidates)
                self.assertEqual(error.exception.code,'LLM_CLINICAL_EDIT_DENIED')
    def test_name_is_removed_and_clinical_spelling_and_values_stay(self):
        p=proposal([{'quote':'한도윤','occurrence':0,'type':'PERSON','replacement':'','reason':'성명'}])
        result,edits=apply_edits(SOURCE,p,[])
        self.assertNotIn('한도윤',result)
        for phrase in ['IUP 38+5주','NSVD','출생체중 2.95 kg','생후 약 25분','NICU']:
            self.assertIn(phrase,result)
    def test_independent_check_flags_expansion_without_trusting_model(self):
        output=SOURCE.replace('IUP 38+5주','임신 38주+5일').replace('NSVD','자연분만')
        result=verify_relations(SOURCE,output,[],'임상 경과 연구',[])
        self.assertFalse(result['eligible'])
        self.assertTrue(any(r['kind']=='gestational_age' and r['status']=='failed' for r in result['rows']))
        self.assertTrue(any(r['kind']=='clinical_abbreviation' and r['status']=='failed' for r in result['rows']))
    def test_same_literal_in_a_name_field_remains_an_identifier(self):
        source='Name: NSVD.\nNSVD로 출생.'
        p=proposal([{'quote':'NSVD','occurrence':0,'type':'PERSON','replacement':'','reason':'명시적 성명 필드'}])
        output,_=apply_edits(source,p,[])
        self.assertEqual(output,'Name: <PERSON_1>.\nNSVD로 출생.')
    def test_clinical_paraphrase_without_privacy_evidence_is_rejected(self):
        source='호흡 보조 중. 발열 없음.'
        p=proposal([{'quote':'호흡 보조 중','occurrence':0,'type':'QUASI','replacement':'산소 치료 중','reason':'더 자연스러운 표현'}])
        p['conditions'][0]['source_quote']='발열 없음'
        with self.assertRaises(Denied):apply_edits(source,p,[])

if __name__=='__main__':unittest.main()
