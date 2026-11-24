"""Mission-scoped bounded attempts; offline, isolated SQLite."""
import tempfile
from pathlib import Path
import unittest
from studio.store import StudioStore, StoreError
from studio.test_backend import VALID_SOURCE

class MissionBudget(unittest.TestCase):
    def test_organic_growth_mission_starts_with_a_bounded_fresh_budget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            store=StudioStore(Path(tmp)/'db',model_mission='organic-growth-v1')
            summary=store.model_budget_summary()
            self.assertEqual(summary['mission'],'organic-growth-v1')
            self.assertEqual(summary['used'],0)
            self.assertEqual(summary['available'],12)

    def test_extended_budget_does_not_reset_legacy_attempts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'db'
            old=StudioStore(path)
            old.import_sources([VALID_SOURCE],'manual_bookmark',False)
            draft=old.create_draft(VALID_SOURCE['id'],'research','Hedef','Okur')
            for _ in range(12):
                job=old.create_job('model_review',draft['id'],1)
                old.start_model_run(job['id'],'model_review')
                old.finish_model_run(job['id'],None,None,False)
            historical=old.model_budget_summary()['calls']
            extra=StudioStore(path,model_mission='extended-modernization-2026-09-08')
            self.assertEqual(extra.model_budget_summary()['used'],0)
            for _ in range(12):
                job=extra.create_job('model_review',draft['id'],1)
                extra.start_model_run(job['id'],'model_review')
                extra.finish_model_run(job['id'],None,None,False)
            job=extra.create_job('model_review',draft['id'],1)
            with self.assertRaises(StoreError):extra.start_model_run(job['id'],'model_review')
            self.assertEqual(old.model_budget_summary()['calls'],historical)
            self.assertEqual(extra.model_budget_summary()['used'],12)
            self.assertEqual(old.model_budget_summary()['used'],12)
            self.assertTrue(all(c['response_id'] is None for c in extra.model_budget_summary()['calls']))
            with self.assertRaises(StoreError):StudioStore(path,model_mission='arbitrary-reset')

if __name__=='__main__':unittest.main()
