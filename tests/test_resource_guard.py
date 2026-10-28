import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ClinicalTextWeb.resource_guard import ResourceGuard,LIMITS
from ClinicalTextWeb.prepared_cache import fingerprint

class ResourceLimitTests(unittest.TestCase):
    def test_limits_keep_single_gpu_work_small_and_fast_queue_separate(self):
        self.assertEqual(LIMITS['audience'],50)
        self.assertEqual(LIMITS['live_per_hour'],12)
        self.assertEqual(LIMITS['live_deadline_seconds'],90)
        self.assertEqual(LIMITS['llm_seconds_per_hour'],600)
        self.assertGreaterEqual(LIMITS['fast_pending'],50)
        self.assertLessEqual(LIMITS['live_pending'],2)
    def test_exhausted_budget_prevents_new_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=ResourceGuard(folder,'http://127.0.0.1:11435','test-model')
            guard.charge(600)
            with patch.object(guard,'gpu_available',return_value=True):self.assertFalse(guard.ready())
            self.assertEqual(guard.remaining(),0)
    def test_busy_or_unobservable_gpu_prevents_new_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=ResourceGuard(folder,'http://127.0.0.1:11435','test-model')
            with patch.object(guard,'gpu_available',return_value=False):self.assertFalse(guard.ready())
    def test_previous_unconfirmed_unload_latches_live_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=ResourceGuard(folder,'http://127.0.0.1:11435','test-model')
            (Path(folder)/'live-paused.json').write_text('{}')
            with patch.object(guard,'gpu_available',return_value=True):self.assertFalse(guard.ready())
    def test_cache_identity_tracks_source_implementation(self):
        first=fingerprint();self.assertEqual(len(first),64);self.assertEqual(first,fingerprint())

if __name__=='__main__':unittest.main()
