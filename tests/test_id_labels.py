import unittest
from ClinicalCDSGateway.gateway.detector import residual_check

class IdentifierLabelTests(unittest.TestCase):
    def test_fully_substituted_id_label_is_not_a_value(self):
        for text in ['병록번호 <ID_1>.', 'MRN: <ID_2>.', '여권번호: <ID_3>.']:
            self.assertNotIn('RESIDUAL_ID',residual_check('synthetic source',text))
    def test_real_value_after_field_label_is_still_blocked(self):
        for text in ['병록번호 A123456.', 'MRN-A123456', '여권번호 M12345678']:
            self.assertIn('RESIDUAL_ID',residual_check('synthetic source',text))
    def test_unrelated_substitution_does_not_hide_id(self):
        for text in ['병록번호 <PERSON_1>.', '병록번호 <ID_1>. MRN-A123456', '주민등록번호 900101-1234567']:
            self.assertIn('RESIDUAL_ID',residual_check('synthetic source',text))

if __name__=='__main__':unittest.main()
