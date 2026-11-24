import tempfile
from pathlib import Path
import unittest
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.launcher import parser

class CapabilityContract(unittest.TestCase):
    def test_test_harness_is_not_advertised_as_running_worker(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            with TestClient(create_app(Path(tmp)/'db',Path(tmp)/'assets',run_jobs=False),base_url='http://127.0.0.1') as client:
                self.assertEqual(client.get('/api/health').json()['model_worker'],'test_harness_disabled')
                self.assertFalse(client.get('/api/state').json()['capabilities']['job_execution'])

    def test_explicit_mission_reaches_application_without_changing_default(self):
        args=parser().parse_args(['--model-mission','extended-modernization-2026-09-08','--no-browser'])
        self.assertEqual(args.model_mission,'extended-modernization-2026-09-08')
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            app=create_app(Path(tmp)/'db',Path(tmp)/'assets',run_jobs=False,model_mission=args.model_mission)
            self.assertEqual(app.state.store.model_budget_summary()['mission'],args.model_mission)
        self.assertEqual(parser().parse_args([]).model_mission,'organic-growth-v1')

if __name__=='__main__':unittest.main()
