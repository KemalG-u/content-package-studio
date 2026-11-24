import unittest
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class TranscribeTests(unittest.TestCase):
    def test_vps_transfer_requires_consent(self):
        from studio.transcribe import transcribe_owned
        with self.assertRaisesRegex(ValueError, 'VPS'):
            transcribe_owned(ROOT/'data/verification-audio/voice.mp3', ROOT/'data/verification-asr', False)

    @unittest.skipUnless(os.environ.get('STUDIO_LIVE_ASR') == '1', 'VPS testi açıkça seçilir')
    def test_actual_turkish_audio_transcribes_to_subtitles(self):
        from studio.transcribe import transcribe_owned
        out = ROOT/'data/verification-asr'
        result = transcribe_owned(ROOT/'data/verification-audio/voice.mp3', out, True)
        text = (out/'transcript.tr.txt').read_text(encoding='utf-8').lower()
        self.assertTrue(any(word in text for word in ['hafıza','tercih','kurallar']), text)
        self.assertIn('-->', (out/'transcript.tr.srt').read_text(encoding='utf-8'))
        self.assertTrue((out/'transcript.tr.vtt').read_text(encoding='utf-8').startswith('WEBVTT'))
        self.assertTrue(result['warnings'])

if __name__ == '__main__':
    unittest.main()
