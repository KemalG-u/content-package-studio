import tempfile
import shutil
import unittest
import json
import subprocess
import os
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent


NEEDS_FFMPEG = unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed")


class MediaTests(unittest.TestCase):
    def test_owned_photo_is_resized_and_metadata_removed(self):
        from studio.media import prepare_photo
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            src, out = Path(tmp)/'input.jpg', Path(tmp)/'photo.jpg'
            image = Image.new('RGB', (1600, 1000), 'green')
            exif = Image.Exif()
            exif[270] = 'private fixture metadata'
            image.save(src, exif=exif)
            result = prepare_photo(src, out, 'owned', 'Yeşil fotoğraf işleme test yüzeyi.')
            with Image.open(out) as processed:
                self.assertFalse(processed.getexif())
                self.assertLessEqual(max(processed.size), 1280)
            self.assertEqual(result['rights'], 'owned')

    @unittest.skipUnless(os.environ.get('STUDIO_LIVE_AUDIO') == '1', 'Açık ağlı ses testi ayrıca çalıştırılır')
    def test_live_audio_and_boundary_subtitles(self):
        from studio.media import build_media
        out = ROOT / 'data' / 'verification-audio'
        result = build_media({'title': 'Bellek notu', 'main': 'Hermes için yeni bir hafıza kurmak gerekmiyor. Kullanıcı tercihleriyle proje kurallarını ayrı tutmak gerekiyor.', 'cloud_tts_allowed': True, 'voice': 'tr-TR-AhmetNeural'}, out, 'audio')
        self.assertGreater((out/'voice.mp3').stat().st_size, 1000)
        self.assertIn('-->', (out/'voice.tr.srt').read_text(encoding='utf-8'))
        info = json.loads(subprocess.check_output(['ffprobe', '-v', 'quiet', '-show_format', '-of', 'json', str(out/'voice.mp3')]))
        self.assertGreater(float(info['format']['duration']), 2)

    def test_audio_requires_explicit_cloud_consent(self):
        from studio.media import build_media
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            with self.assertRaisesRegex(ValueError, 'bulut'):
                build_media({'main': 'Test metni', 'cloud_tts_allowed': False}, Path(tmp), 'audio')

    @NEEDS_FFMPEG
    def test_video_has_three_scenes_and_timed_captions(self):
        from studio.media import build_media
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            out = Path(tmp)
            result = build_media({'title': 'Bellek düzeni', 'main': 'Bilgiyi doğru yerde sakla.', 'scene_texts': ['Kimlik kişilik ve üsluptur.', 'Kullanıcı tercihleri ayrı tutulur.', 'Proje kuralları projede kalır.']}, out, 'video')
            self.assertTrue((out / 'video.mp4').is_file())
            report = json.loads(subprocess.check_output(['ffprobe', '-v', 'quiet', '-show_format', '-show_streams', '-of', 'json', str(out/'video.mp4')]))
            self.assertAlmostEqual(float(report['format']['duration']), 12, places=1)
            self.assertEqual(report['streams'][0]['codec_name'], 'h264')
            self.assertEqual((out/'captions.tr.srt').read_text(encoding='utf-8').count('-->'), 3)
            self.assertIn('WEBVTT', (out/'captions.tr.vtt').read_text(encoding='utf-8'))
            self.assertEqual({item['name']: item.get('for_asset') for item in result['files'] if item['kind'] == 'subtitle'}, {'captions.tr.srt': 'video.mp4', 'captions.tr.vtt': 'video.mp4'})
            self.assertNotEqual((out/'scene-01.png').read_bytes(), (out/'scene-03.png').read_bytes())

    def test_card_is_real_png_with_turkish_alt(self):
        from studio.media import build_media
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            out = Path(tmp)
            draft = {'id': 'test', 'revision': 1, 'title': 'Hafıza nerede?', 'main': 'Kimlik, kullanıcı ve proje kurallarını aynı notta karıştırma.', 'mode': 'explainer'}
            result = build_media(draft, out, 'card')
            self.assertEqual(result['files'][0]['name'], 'card.png')
            with Image.open(out / 'card.png') as im:
                self.assertEqual(im.size, (1280, 720))
                self.assertEqual(im.format, 'PNG')
            self.assertIn('Kimlik', result['alt_text'])


if __name__ == '__main__':
    unittest.main()
