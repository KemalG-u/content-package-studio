"""Pinned upstream twitter-text, rather than a Python len() approximation."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
_SCRIPT = "const fs=require('fs'); const t=require('twitter-text'); const rows=JSON.parse(fs.readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(rows.map(s=>{const r=t.parseTweet(s);return {weighted_length:r.weightedLength,valid:r.valid}})));"


def measure_many(texts: list[str]) -> list[dict]:
    if not isinstance(texts, list) or len(texts) > 20 or any(not isinstance(t,str) or len(t)>20000 for t in texts):
        raise ValueError('Metin sayacı girdisi sınırı aşıyor.')
    node = shutil.which('node')
    if not node:
        raise ValueError('Node bulunamadı; X sayacı doğrulanamadı.')
    result = subprocess.run([node, '-e', _SCRIPT], cwd=ROOT, input=json.dumps(texts,ensure_ascii=False).encode('utf-8'), capture_output=True, timeout=5)
    if result.returncode:
        raise ValueError('X metin sayacı çalışmadı; yaklaşık sayım doğru diye sunulmadı.')
    return json.loads(result.stdout)
