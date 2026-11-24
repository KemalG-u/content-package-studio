import unittest
import tempfile
import time
from fastapi.testclient import TestClient
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from studio.store import StudioStore, RevisionConflict
from studio.test_backend import VALID_SOURCE

class RaceTests(unittest.TestCase):
    def test_edit_during_media_job_ends_failed_not_running(self):
        from studio.app import create_app
        from studio.media import build_media
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            app=create_app(Path(tmp)/'db.sqlite', Path(tmp)/'assets')
            store=app.state.store
            store.import_sources([VALID_SOURCE], 'manual_bookmark', False)
            d=store.create_draft(VALID_SOURCE['id'],'explainer','test','okur')
            d=store.update_draft(d['id'],1,{'main':'Kısa test metni.','title':'Test'})
            def render_then_edit(draft,out_dir,kind):
                result=build_media(draft,out_dir,kind)
                store.update_draft(draft['id'],draft['revision'],{'title':'Sonraki başlık'})
                return result
            with patch('studio.app.build_media',side_effect=render_then_edit):
                with TestClient(app,base_url='http://127.0.0.1') as c:
                    job=c.post(f"/api/drafts/{d['id']}/media",json={'revision':2,'kind':'card'},headers={'origin':'http://127.0.0.1'}).json()['job_id']
                    for _ in range(50):
                        state=next(j for j in store.list_jobs() if j['id']==job)
                        if state['status'] in {'succeeded','failed'}: break
                        time.sleep(.03)
                    self.assertEqual(state['status'],'failed')

    def test_parallel_same_revision_saves_conflict_instead_of_losing_data(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            store=StudioStore(Path(tmp)/'db.sqlite')
            store.import_sources([VALID_SOURCE], 'manual_bookmark', False)
            d=store.create_draft(VALID_SOURCE['id'],'explainer','test','okur')
            barrier=Barrier(2)
            original=StudioStore._decode_draft
            def sync_decode(row):
                decoded=original(row)
                barrier.wait(timeout=5)
                return decoded
            def edit(field):
                try:
                    store.update_draft(d['id'],1,{field:field})
                    return 'saved'
                except RevisionConflict:
                    return 'conflict'
            with patch.object(StudioStore,'_decode_draft',staticmethod(sync_decode)):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results=list(pool.map(edit,['title','main']))
            self.assertCountEqual(results,['saved','conflict'])

if __name__=='__main__':
    unittest.main()
