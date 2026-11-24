"""Provider transport failures never expose subprocess text to the UI."""
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from studio.model_worker import run_real_model
from studio.store import StoreError

class SafeWorkerFailure(unittest.TestCase):
    def test_nonzero_worker_does_not_echo_untrusted_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'run_agent.py').write_text('')
            with patch('studio.model_worker.HERMES_ROOT',Path(tmp)), patch('studio.model_worker.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','Traceback: example-private-value')):
                with self.assertRaises(StoreError) as caught:
                    run_real_model('Sentetik istem',Path(tmp)/'worker.jsonl')
            error=str(caught.exception)
            self.assertNotIn('Traceback',error)
            self.assertNotIn('example-private-value',error)
            self.assertIn('korundu',error)
            self.assertIn('yeniden',error)

    def test_timeout_preserves_copy_and_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'run_agent.py').write_text('')
            with patch('studio.model_worker.HERMES_ROOT',Path(tmp)), patch('studio.model_worker.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',1)):
                with self.assertRaises(StoreError) as caught:
                    run_real_model('Sentetik istem',Path(tmp)/'worker.jsonl')
            self.assertIn('zaman aşımı',str(caught.exception))
            self.assertIn('korundu',str(caught.exception))

if __name__=='__main__':unittest.main()
