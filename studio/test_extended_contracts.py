"""Additional acceptance checks for already present contracts, isolated D: DB."""
from contextlib import closing
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import shutil
import unittest
import zipfile
from fastapi.testclient import TestClient
from studio.app import create_app
from studio.editorial import approval_fingerprint
from studio.store import StudioStore, StoreError
from studio.test_backend import VALID_SOURCE
from studio.test_editorial_fixtures import original_editorial_payload

NEEDS_FFMPEG = unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")


class ExtendedContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.tmp.name)
        self.app=create_app(self.root/'db',self.root/'assets',run_jobs=False)
        self.store=self.app.state.store
        self.store.import_sources([VALID_SOURCE],'manual_bookmark',False)
        self.draft=self.store.create_draft(VALID_SOURCE['id'],'explainer','Açıkla','Test okuru')
        self.client=TestClient(self.app,base_url='http://127.0.0.1')
    def tearDown(self):
        self.client.close(); self.tmp.cleanup()
    def ready(self):
        payload=original_editorial_payload(self.store.list_angles(self.draft['id'])['angles'][0])
        return self.store.update_draft(self.draft['id'],self.draft['revision'],payload)
    def test_empty_research_map_allows_first_generation_but_never_approval(self):
        draft=self.ready();brief=copy.deepcopy(draft['reader_brief']);brief['provenance']='researched_external'
        draft=self.store.update_draft(draft['id'],draft['revision'],{'reader_brief':brief,'claim_map':{'claims':[],'evidence':[]}})
        self.store.require_editorial_ready(draft['id'],draft['revision'],for_generation=True)
        with self.assertRaises(StoreError):
            self.store.approve_copy(draft['id'],draft['revision'],approval_fingerprint(draft))
        self.assertFalse(self.store.get_draft(draft['id'])['copy_approved'])
        self.assertEqual(self.client.get(f"/api/drafts/{draft['id']}/export").status_code,200)

    @NEEDS_FFMPEG
    def test_reserved_video_filename_never_overwrites_zip_documents(self):
        import subprocess, base64, hashlib
        clip=self.root/'clip.mp4'
        proc=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:d=0.1','-c:v','libx264','-pix_fmt','yuv420p',str(clip)],capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stderr.decode())
        draft=self.ready()
        for name in ('manifest.json','main.txt','alt-text.txt','MODEL-REVIEW.JSON'):
            with self.subTest(name=name):
                response=self.client.post(f"/api/drafts/{draft['id']}/upload",headers={'Origin':'http://127.0.0.1'},json={'revision':draft['revision'],'kind':'video','filename':name,'rights':'owned','alt_text':'Mavi örnek video','data_base64':base64.b64encode(clip.read_bytes()).decode()})
                self.assertEqual(response.status_code,201,response.text)
                exported=self.client.get(f"/api/drafts/{draft['id']}/export")
                self.assertEqual(exported.status_code,200,exported.text if exported.status_code!=200 else '')
                with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
                    manifest=json.loads(z.read('manifest.json'))
                    self.assertEqual(z.read('main.txt').decode(),draft['main'])
                    for asset in manifest['assets']:
                        self.assertEqual(hashlib.sha256(z.read(asset['package_name'])).hexdigest(),asset['sha256'])
                    for member,digest in manifest['files'].items():
                        self.assertEqual(hashlib.sha256(z.read(member)).hexdigest(),digest)
                    self.assertIsNone(z.testzip())

    def test_success_retains_historical_failed_revision_count_not_duplicate_clicks(self):
        failed=self.store.save_deterministic_review(self.draft['id'],1)
        self.assertEqual(failed['failed_revision_count'],1)
        draft=self.ready()
        passed=self.store.save_deterministic_review(draft['id'],draft['revision'])
        self.assertTrue(passed['passed']); self.assertEqual(passed['failed_revision_count'],0)
        # Brief changed from incomplete to complete: historical failures have another fingerprint.
        again=self.store.save_deterministic_review(draft['id'],draft['revision'])
        self.assertEqual(passed,again)
        broken=self.store.update_draft(draft['id'],draft['revision'],{'main':'Haritasız yeni metin'})
        failed=self.store.save_deterministic_review(broken['id'],broken['revision'])
        self.assertEqual(failed['failed_revision_count'],1)
        fixed=self.store.update_draft(broken['id'],broken['revision'],original_editorial_payload(self.store.list_angles(broken['id'])['angles'][0]))
        success=self.store.save_deterministic_review(fixed['id'],fixed['revision'])
        self.assertTrue(success['passed']);self.assertEqual(success['failed_revision_count'],1)
        self.assertEqual(success['next_action'],'ready_for_human_review')
    def test_tone_depth_persist_export_restore_and_restart_without_user_voice_claim(self):
        draft=self.ready();old_rev=draft['revision'];old_brief=draft['reader_brief']
        brief={**old_brief,'tone':'technical','depth':'advanced'}
        changed=self.store.update_draft(draft['id'],old_rev,{'reader_brief':brief})
        exported=self.client.get(f"/api/drafts/{draft['id']}/export")
        self.assertEqual(exported.status_code,200,exported.text if exported.status_code!=200 else '')
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            self.assertEqual(json.loads(archive.read('reader-brief.json')),brief)
        reopened=StudioStore(self.root/'db');self.assertEqual(reopened.get_draft(draft['id'])['reader_brief'],brief)
        restored=reopened.restore_draft(draft['id'],changed['revision'],old_rev)
        self.assertEqual(restored['reader_brief'],old_brief);self.assertFalse(restored['copy_approved'])
    def test_every_copy_source_claim_media_accessibility_rights_fingerprint_field(self):
        draft=self.ready()
        draft.update(source_evidence={'note':'test'},primary_media={'name':'a','revision':2,'sha256':'a'*64},assets=[{'name':'a','revision':2,'sha256':'a'*64,'alt_text':'İlk','rights':'owned','for_asset':'clip.mp4'}])
        before=approval_fingerprint(draft);self.assertEqual(before,approval_fingerprint(copy.deepcopy(draft)))
        for key,value in [('main','Farklı'),('reply','Farklı yanıt'),('source_evidence',{'note':'other'}),('primary_media',None),('alt_text','Diğer')]:
            with self.subTest(field=key):
                changed=copy.deepcopy(draft);changed[key]=value
                self.assertNotEqual(before,approval_fingerprint(changed))
        for key,value in [('alt_text','Yeni'),('for_asset','another.mp4'),('rights','licensed')]:
            changed=copy.deepcopy(draft);changed['assets'][0][key]=value
            self.assertNotEqual(before,approval_fingerprint(changed))
        changed=copy.deepcopy(draft);changed['editorial']['claim_map']['claims'][0]['text']='Farklı iddia'
        self.assertNotEqual(before,approval_fingerprint(changed))
    def test_interruption_never_restarts_model_and_retry_uses_current_revision(self):
        draft=self.ready()
        job=self.store.create_job('generate',draft['id'],draft['revision'])
        reopened=StudioStore(self.root/'db')
        interrupted=reopened.list_jobs()[-1]
        self.assertEqual(interrupted['status'],'failed');self.assertEqual(interrupted['detail'],'failedInterrupted')
        self.assertEqual(reopened.model_budget_summary()['used'],0)
        changed=reopened.update_draft(draft['id'],draft['revision'],{'feedback':'Yeni talimat'})
        with self.assertRaises(StoreError):reopened.create_job('generate',draft['id'],draft['revision'])
        explicit=reopened.create_job('generate',draft['id'],changed['revision'])
        self.assertEqual(explicit['revision'],changed['revision']);self.assertEqual(reopened.model_budget_summary()['used'],0)

if __name__=='__main__':unittest.main()
