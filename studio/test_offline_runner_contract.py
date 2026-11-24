import os,unittest
from unittest.mock import patch
from studio import verify_offline

class OfflineReportContract(unittest.TestCase):
 def test_default_is_latest_not_a_historical_mission_report(self):
  with patch.dict(os.environ,{},clear=True):
   self.assertEqual(verify_offline.report_path(),verify_offline.ROOT/'reports/offline-latest.json')
 def test_explicit_report_stays_inside_project_reports(self):
  path=verify_offline.ROOT/'reports/extended-modernization-2026-09-08/offline-tests.json'
  with patch.dict(os.environ,{'STUDIO_OFFLINE_REPORT':str(path)}):
   self.assertEqual(verify_offline.report_path(),path)
  with patch.dict(os.environ,{'STUDIO_OFFLINE_REPORT':'C:/invalid-studio-report.json'}):
   with self.assertRaises(ValueError):verify_offline.report_path()

if __name__=='__main__':unittest.main()
