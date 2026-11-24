"""Text-first local explainer template acceptance; no external assets/services."""
from pathlib import Path
import tempfile
import unittest
from PIL import Image

class ExplainerTests(unittest.TestCase):
    def test_png_and_overflow_are_explicit(self):
        from studio.explanatory import render_explanation
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path=Path(directory)/'explanation.png'
            result=render_explanation('Bir yöntem önerisi',[('Kural','Her iddiayı bir pasajla eşleştir.'),('Sınır','Bağlantı tek başına destek değildir.')],path)
            with Image.open(path) as image:
                image.verify()
            self.assertEqual(result['width'],840)
            self.assertIn('uygulama ekranı değildir',result['limitation'])
            with self.assertRaises(ValueError):
                render_explanation('Taşma',[('Kural','x'*900)],Path(directory)/'invalid.png')
            self.assertFalse((Path(directory)/'invalid.png').exists())

if __name__=='__main__':unittest.main(verbosity=2)
