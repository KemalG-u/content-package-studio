import unittest
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from studio import text_budget

class TextBudgetTests(unittest.TestCase):
    def test_counter_api_and_generated_text_reject_overflow(self):
        from studio.app import create_app
        from studio.generator import parse_generated_draft
        from studio.store import StoreError
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            app = create_app(Path(tmp)/'db', Path(tmp)/'assets', run_jobs=False)
            with TestClient(app, base_url='http://127.0.0.1') as c:
                r = c.post('/api/measure', json={'texts':['Türkçe','👨‍👩‍👧‍👦']}, headers={'origin':'http://127.0.0.1'})
                self.assertEqual(r.status_code,200)
                self.assertEqual([r['weighted_length'] for r in r.json()['results']],[6,2])
        with self.assertRaises(StoreError):
            parse_generated_draft(json.dumps({'title':'Başlık','main':'a'*281,'reply':'','scene_texts':['bir','iki','üç']}))

    def test_official_x_weighting_handles_turkish_emoji_and_urls(self):
        rows = text_budget.measure_many(['Türkçe', '👨‍👩‍👧‍👦', 'https://example.com/uzun/baglanti', 'a'*281])
        self.assertEqual([r['weighted_length'] for r in rows], [6,2,23,281])
        self.assertFalse(rows[-1]['valid'])

if __name__ == '__main__':
    unittest.main()
