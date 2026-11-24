import unittest
import tempfile
import hashlib
import zipfile
import json
from pathlib import Path
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.test_backend import VALID_SOURCE

class LocalPackageTests(unittest.TestCase):
    def test_package_is_written_under_project_output_and_revision_checked(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            app=create_app(Path(tmp)/'db.sqlite',Path(tmp)/'assets',run_jobs=False)
            store=app.state.store
            store.import_sources([VALID_SOURCE],'manual_bookmark',False)
            d=store.create_draft(VALID_SOURCE['id'],'explainer','test','okur')
            d=store.update_draft(d['id'],1,{'main':'Kısa test metni.'})
            with TestClient(app,base_url='http://127.0.0.1') as c:
                h={'origin':'http://127.0.0.1'}
                r=c.post(f"/api/drafts/{d['id']}/save-package",headers=h,json={'revision':d['revision']})
                self.assertEqual(r.status_code,200)
                body=r.json();path=Path(body['path'])
                self.assertTrue(path.is_file())
                self.assertEqual(path.parent,Path(tmp)/'exports')
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),body['sha256'])
                with zipfile.ZipFile(path) as z:
                    self.assertIsNone(z.testzip())
                    self.assertFalse(json.loads(z.read('manifest.json'))['publication_ready'])
                bad=c.post(f"/api/drafts/{d['id']}/save-package",headers=h,json={'revision':1})
                self.assertEqual(bad.status_code,409)
                injected=c.post(f"/api/drafts/{d['id']}/save-package",headers=h,json={'revision':2,'path':'C:/evil.zip'})
                self.assertEqual(injected.status_code,422)

if __name__=='__main__':
    unittest.main()
