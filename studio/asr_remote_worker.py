"""Fixed program sent over SSH; no shell/user-supplied commands or downloads."""
import base64
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    import psutil
    if psutil.virtual_memory().available < 3 * 1024**3:
        raise ValueError('VPS kullanılabilir belleği 3 GiB altında.')
    root = Path.home() / 'karincaai-studio-worker'
    root.mkdir(mode=0o700, exist_ok=True)
    lock = (root/'worker.lock').open('a')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    payload = json.loads(sys.stdin.buffer.read(9 * 1024**2 + 1))
    raw = base64.b64decode(payload['data'], validate=True)
    if not raw or len(raw) > 6 * 1024**2:
        raise ValueError('Ses dosyası sınırı aşıldı.')
    source_hash = hashlib.sha256(raw).hexdigest()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='job-', dir=root) as tmp:
        media = Path(tmp)/'input.media'
        media.write_bytes(raw)
        probe = subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_streams','-show_format','-of','json', str(media)], capture_output=True, timeout=15, check=True)
        info = json.loads(probe.stdout)
        if not any(s.get('codec_type') == 'audio' for s in info['streams']):
            raise ValueError('Dosyada ses akışı yok.')
        duration = float(info['format']['duration'])
        if not 0 < duration <= 180:
            raise ValueError('En fazla 180 saniyelik kayıt desteklenir.')
        wav = Path(tmp)/'input.wav'
        subprocess.run(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(media),'-vn','-ac','1','-ar','16000','-threads','2',str(wav)], capture_output=True, timeout=30, check=True)
        from faster_whisper import WhisperModel
        model = WhisperModel('medium', device='cpu', compute_type='int8', cpu_threads=2, num_workers=1, local_files_only=True)
        segments, _ = model.transcribe(str(wav), language='tr', beam_size=1, vad_filter=True, condition_on_previous_text=False)
        rows = [{'start': float(s.start), 'end': min(float(s.end), duration), 'text': s.text.strip()} for s in segments if s.text.strip()]
        if not rows:
            raise ValueError('Anlaşılır konuşma bulunamadı.')
    # TemporaryDirectory has already removed the input and decoded audio.
    print(json.dumps({'segments': rows, 'duration': duration, 'source_sha256': source_hash, 'model': 'faster-whisper-medium-int8', 'language': 'tr', 'elapsed_seconds': round(time.monotonic()-start,3), 'temporary_input_removed': not Path(tmp).exists()}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__ + ': ' + str(exc)[:200]}, ensure_ascii=False))
        sys.exit(1)
