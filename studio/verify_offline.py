"""Run Studio tests without a live model, cloud TTS, or VPS transcription.

The existing backend live-model test predates opt-in markers. Exclude it by
exact name and list it in the report; do not silently turn it into a mocked pass.
"""
from pathlib import Path
import json
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = 'test_backend.BackendContractTests.test_generation_route_uses_isolated_real_hermes_worker'


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def report_path():
    path=Path(os.environ.get('STUDIO_OFFLINE_REPORT',str(ROOT/'reports/offline-latest.json'))).resolve()
    if not path.is_relative_to((ROOT/'reports').resolve()):
        raise ValueError('Çevrimdışı rapor proje reports klasörü altında olmalı.')
    return path

def main():
    os.environ['STUDIO_LIVE_AUDIO'] = '0'
    os.environ['STUDIO_LIVE_ASR'] = '0'
    sys.path.insert(0, str(ROOT))
    tests = list(flatten(unittest.defaultTestLoader.discover(str(ROOT / 'studio'), pattern='test_*.py')))
    excluded = [test.id() for test in tests if test.id() == EXCLUDED]
    if excluded != [EXCLUDED]:
        raise RuntimeError('Canlı model testinin seçimi değişti; çevrimdışı kapsam yeniden incelenmeli.')
    selected = [test for test in tests if test.id() not in excluded]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    proof = {
        'scope': 'Studio offline suite; real model test explicitly excluded',
        'discovered': len(tests), 'selected': len(selected), 'run': result.testsRun,
        'passed': result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
        'skipped': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped], 'excluded': excluded,
        'failures': [test.id() for test, _ in result.failures],
        'errors': [test.id() for test, _ in result.errors],
        'successful': result.wasSuccessful(),
        'live_audio': False, 'live_asr': False, 'live_model': False,
    }
    output = report_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(proof, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
