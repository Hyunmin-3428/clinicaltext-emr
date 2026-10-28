import unittest
from ClinicalTextWeb.input_text import normalize_text
from ClinicalCDSGateway.gateway.common import Denied

class CompatibilityInputTests(unittest.TestCase):
    def test_full_width_dates_digits_and_clinical_units_are_accepted(self):
        self.assertEqual(normalize_text('２０２４년 ５월 ２일, cefazolin １ ㎎ ＩＶ'),'2024년 5월 2일, cefazolin 1 mg IV')
    def test_windows_line_breaks_and_compatible_number_markers(self):
        self.assertEqual(normalize_text('① 발열 없음\r\n② 수술 ２일 후'),'1 발열 없음\n2 수술 2일 후')
    def test_normalization_is_idempotent(self):
        value=normalize_text('ＢＰ １２０／８０ ｍｍＨｇ')
        self.assertEqual(normalize_text(value),value)
    def test_hidden_controls_and_disguised_script_still_rejected(self):
        for value in ['기록\u202eabc','＜ｓｃｒｉｐｔ＞alert(1)']:
            with self.assertRaises(Denied):normalize_text(value)

if __name__=='__main__':unittest.main()
