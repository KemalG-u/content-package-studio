"""Explicitly consented, local-owned media to the user's existing SSH VPS."""
from pathlib import Path
import base64
import os
import hashlib
import json
import shlex
import subprocess


def transcribe_owned(input_path: Path, out_dir: Path, vps_allowed: bool) -> dict:
    if vps_allowed is not True:
        raise ValueError('Seçilen dosyanın kendi VPS sunucunuza aktarımı için izin gerekli.')
    input_path, out_dir = Path(input_path), Path(out_dir)
    if not input_path.is_file() or not 0 < input_path.stat().st_size <= 6 * 1024**2:
        raise ValueError('Altyazı dosyası yok, boş veya 6 MiB sınırını aşıyor.')
    raw = input_path.read_bytes()
    output_prefix = f"transcript-{input_path.stem}-{hashlib.sha256(raw).hexdigest()[:12]}"
    program = Path(__file__).with_name('asr_remote_worker.py').read_text(encoding='utf-8')
    command = 'timeout 180s python3 -c ' + shlex.quote(program)
    try:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', os.environ.get('STUDIO_ASR_SSH_HOST', 'vps'), command], input=json.dumps({'data': base64.b64encode(raw).decode('ascii')}).encode(), capture_output=True, timeout=200)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError('VPS altyazı işçisi açılamadı veya zaman sınırını aştı.') from exc
    if result.returncode:
        raise ValueError('VPS altyazı işçisi tamamlayamadı. Bağlantı, model önbelleği veya kaynak sınırını kontrol edin.')
    try:
        data = json.loads(result.stdout)
        if data['source_sha256'] != hashlib.sha256(raw).hexdigest() or data['temporary_input_removed'] is not True:
            raise ValueError('Altyazı kaynak/temizlik doğrulaması başarısız.')
        rows = data['segments']
        duration = float(data['duration'])
        if not isinstance(rows, list) or not rows or len(rows) > 500:
            raise ValueError('Altyazı bölümleri geçersiz.')
        previous = 0
        for row in rows:
            if not isinstance(row['text'], str) or not previous <= row['start'] < row['end'] <= duration + 0.1:
                raise ValueError('Altyazı zamanları geçersiz.')
            previous = row['end']
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('VPS geçerli altyazı sonucu döndürmedi.') from exc
    def timestamp(seconds, decimal):
        milliseconds = round(seconds * 1000)
        hours, rem = divmod(milliseconds, 3600000)
        minutes, rem = divmod(rem, 60000)
        secs, millis = divmod(rem, 1000)
        return f'{hours:02d}:{minutes:02d}:{secs:02d}{decimal}{millis:03d}'
    blocks, web = [], ['WEBVTT\n']
    for i, row in enumerate(rows, 1):
        blocks.append(f"{i}\n{timestamp(row['start'], ',')} --> {timestamp(row['end'], ',')}\n{row['text']}\n")
        web.append(f"{timestamp(row['start'], '.')} --> {timestamp(row['end'], '.')}\n{row['text']}\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {
        'srt': f'{output_prefix}.tr.srt', 'vtt': f'{output_prefix}.tr.vtt',
        'text': f'{output_prefix}.tr.txt', 'evidence': f'{output_prefix}-evidence.json',
    }
    (out_dir / names['srt']).write_text('\n'.join(blocks), encoding='utf-8')
    (out_dir / names['vtt']).write_text('\n'.join(web), encoding='utf-8')
    (out_dir / names['text']).write_text('\n'.join(r['text'] for r in rows), encoding='utf-8')
    (out_dir / names['evidence']).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'kind': 'transcript', 'files': [{'name': names['srt'], 'kind': 'subtitle', 'mime': 'application/x-subrip', 'for_asset': input_path.name}, {'name': names['vtt'], 'kind': 'subtitle', 'mime': 'text/vtt', 'for_asset': input_path.name}, {'name': names['text'], 'kind': 'transcript', 'mime': 'text/plain'}, {'name': names['evidence'], 'kind': 'evidence', 'mime': 'application/json'}], 'alt_text': '', 'warnings': ['Kendi VPS sunucunuzdaki mevcut çok dilli model kullanıldı; konuşma metni insan kontrolü bekler.', 'Geçici sunucu ses kopyası temizlendi.']}
