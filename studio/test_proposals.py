"""Real job processor with deterministic worker substitute; no provider call."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.editorial import MODEL_REVIEW_KEYS
from studio.store import StudioStore, RevisionConflict
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import original_editorial_payload

class ProposalFlow(unittest.TestCase):
    def test_successful_discard_wins_before_apply_transaction(self):
        import threading
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            store=StudioStore(Path(folder)/'db.sqlite3'); store.import_sources([VALID_SOURCE],'manual_bookmark',False)
            draft=store.create_draft(VALID_SOURCE['id'],'explainer','Açıkla','Okur')
            store.save_generated_proposal({'id':'race','draft_id':draft['id'],'revision':draft['revision']},{'title':'Öneri','main':'Yeni metin','reply':'','scene_texts':[]})
            read=threading.Event(); release=threading.Event(); errors=[]
            original=store.apply_generated_draft
            def pause(*args,**kwargs):
                read.set(); self.assertTrue(release.wait(5)); return original(*args,**kwargs)
            def apply():
                try: store.apply_proposal(draft['id'],'race',draft['revision'])
                except Exception as exc: errors.append(exc)
            with patch.object(store,'apply_generated_draft',side_effect=pause):
                thread=threading.Thread(target=apply); thread.start(); self.assertTrue(read.wait(5))
                self.assertEqual(store.discard_proposal(draft['id'],'race',draft['revision']),{'status':'discarded'})
                release.set(); thread.join(5); self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors),1,'Başarılı vazgeç sonrası uygula reddedilmeli')
            self.assertIsInstance(errors[0],RevisionConflict)
            self.assertEqual(store.get_draft(draft['id'])['main'],draft['main'])
            self.assertEqual(store.list_proposals()[0]['status'],'discarded')

    def test_same_revision_media_change_rejects_late_model_review(self):
        import json
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp); app=create_app(root/'db',root/'assets');store=app.state.store
            store.import_sources([VALID_SOURCE],'manual_bookmark',False)
            draft=store.create_draft(VALID_SOURCE['id'],'explainer','Açıkla','Okur')
            draft=store.update_draft(draft['id'],1,original_editorial_payload(store.list_angles(draft['id'])['angles'][0]))
            def late(*args):
                folder=root/'assets'/draft['id']/('r'+str(draft['revision']));folder.mkdir(parents=True)
                (folder/'proof.txt').write_bytes(b'isolated race test')
                store.register_media(draft['id'],draft['revision'],folder,{'files':[{'name':'proof.txt','kind':'document','mime':'text/plain'}],'rights':'owned'})
                review={'summary':'Aktif ölçütler değerlendirildi','questions':[{'key':k,'ok':True,'reason':'Somut gerekçe'} for k in MODEL_REVIEW_KEYS],'recommended_action':'ready_for_human_review'}
                return {'text':json.dumps(review),'response_id':None,'output_sha256':'a'*64}
            with TestClient(app,base_url='http://127.0.0.1') as client, patch('studio.app.run_real_model',side_effect=late):
                made=client.post(f"/api/drafts/{draft['id']}/model-review",headers={'origin':'http://127.0.0.1'},json={'revision':draft['revision']})
                self.assertEqual(made.status_code,202,made.text);app.state.job_queue.join()
                self.assertEqual(store.list_jobs()[-1]['status'],'failed')
                self.assertEqual(store.get_draft(draft['id'])['reviews']['model'],[])

    def test_worker_proposal_does_not_change_copy_until_explicit_apply(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            app=create_app(Path(tmp)/'db',Path(tmp)/'assets')
            store=app.state.store
            store.import_sources([VALID_SOURCE],'manual_bookmark',False)
            draft=store.create_draft(VALID_SOURCE['id'],'explainer','Açıkla','Okur')
            draft=store.update_draft(draft['id'],1,original_editorial_payload(store.list_angles(draft['id'])['angles'][0]))
            with TestClient(app,base_url='http://127.0.0.1') as client, patch('studio.app.run_real_model',return_value={'text':'{"title":"Öneri","main":"Modelin önerdiği metin.","reply":"","scene_texts":[]}', 'response_id':None,'output_sha256':'a'*64}):
                headers={'origin':'http://127.0.0.1'}
                made=client.post(f"/api/drafts/{draft['id']}/generate",headers=headers,json={'revision':draft['revision'],'instruction':'Bir öneri üret.'})
                self.assertEqual(made.status_code,202,made.text)
                app.state.job_queue.join()
                state=client.get('/api/state').json()
                self.assertEqual(store.get_draft(draft['id'])['main'],draft['main'])
                proposal=state['proposals'][0]
                self.assertEqual(proposal['status'],'pending')
                url=f"/api/drafts/{draft['id']}/proposals/{proposal['id']}/apply"
                applied=client.post(url,headers=headers,json={'revision':draft['revision']})
                self.assertEqual(applied.status_code,200,applied.text)
                self.assertEqual(applied.json()['main'],'Modelin önerdiği metin.')
                self.assertFalse(applied.json()['copy_approved'])
                self.assertTrue(any(h['main']==draft['main'] for h in store.list_history(draft['id'])))
                self.assertEqual(client.post(url,headers=headers,json={'revision':draft['revision']}).status_code,409)
                self.assertEqual(client.get('/api/state').json()['proposals'][0]['status'],'applied')
                current=store.get_draft(draft['id'])
                store.save_generated_proposal({'id':'discard-case','draft_id':draft['id'],'revision':current['revision']}, {'title':'Atıl öneri','main':'Uygulanmayacak.','reply':'','scene_texts':[]})
                discarded=client.post(f"/api/drafts/{draft['id']}/proposals/discard-case/discard",headers=headers,json={'revision':current['revision']})
                self.assertEqual(discarded.status_code,200,discarded.text)
                self.assertEqual(store.get_draft(draft['id'])['main'],current['main'])
                store.save_generated_proposal({'id':'stale-case','draft_id':draft['id'],'revision':current['revision']}, {'title':'Eski öneri','main':'Geç çıktı','reply':'','scene_texts':[]})
                store.update_draft(draft['id'],current['revision'],{'main':'Daha yeni kullanıcı kaydı'})
                stale=client.post(f"/api/drafts/{draft['id']}/proposals/stale-case/apply",headers=headers,json={'revision':current['revision']})
                self.assertEqual(stale.status_code,409,stale.text)
                self.assertEqual(store.get_draft(draft['id'])['main'],'Daha yeni kullanıcı kaydı')

if __name__=='__main__':unittest.main()
