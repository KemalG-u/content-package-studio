"""Local original media. Never downloads a source image or publishes to X."""
from pathlib import Path
import shutil
import subprocess
import psutil
import asyncio
from PIL import Image, ImageDraw, ImageFont
from PIL import ImageOps

W, H = 1280, 720
FONTS = [Path('C:/Windows/Fonts/bahnschrift.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]


def prepare_photo(input_path, out_path, rights, alt_text):
    if rights not in {'owned', 'licensed'}:
        raise ValueError('Fotoğraf için sahiplik veya lisans beyanı gerekli.')
    if not isinstance(alt_text, str) or not alt_text.strip() or len(alt_text) > 1000:
        raise ValueError('Fotoğraf açıklaması 1–1000 karakter olmalı.')
    with Image.open(input_path) as source:
        if source.format not in {'JPEG', 'PNG', 'WEBP'} or source.width * source.height > 20_000_000:
            raise ValueError('Fotoğraf biçimi veya piksel boyutu desteklenmiyor.')
        image = ImageOps.exif_transpose(source).convert('RGB')
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        clean = Image.new('RGB', image.size)
        clean.paste(image)
        clean.save(out_path, format='JPEG', quality=90)
    return {'name': Path(out_path).name, 'rights': rights, 'alt_text': alt_text, 'metadata_removed': True}


def _font(size):
    for path in FONTS:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise ValueError('Türkçe destekli yazı tipi bulunamadı.')


def _wrap(draw, text, font, max_width):
    lines = []
    for paragraph in text.splitlines():
        current = ''
        for word in paragraph.split():
            if draw.textlength(word, font=font) > max_width:
                raise ValueError('Görsele sığmayan uzun kelime var; metni kısaltın.')
            trial = f'{current} {word}'.strip()
            if draw.textlength(trial, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        elif paragraph == '':
            lines.append('')
    return lines


def _card(title, body, path, label='AÇIKLAYICI GÖRSEL'):
    title, body = str(title).strip(), str(body).strip()
    if not body or len(body) > 1600 or len(title) > 140:
        raise ValueError('Görsel için kısa bir başlık ve dolu metin gerekli.')
    im = Image.new('RGB', (W, H), '#f5f0e6')
    draw = ImageDraw.Draw(im)
    draw.rectangle((0, 0, 22, H), fill='#cc4b25')
    draw.text((72, 42), 'KARINCAAI  /  ' + label, font=_font(20), fill='#71412c')
    draw.line((72, 92, 1208, 92), fill='#c8bca9', width=2)
    heading = _wrap(draw, title, _font(48), 1120)
    if len(heading) > 2:
        raise ValueError('Başlık 2 satırı aşıyor; daha kısa yazın.')
    y = 122
    for line in heading:
        draw.text((72, y), line, font=_font(48), fill='#202d2c')
        y += 60
    y += 30
    font = _font(34)
    lines = _wrap(draw, body, font, 1100)
    if y + len(lines) * 44 > 618:
        font = _font(28)
        lines = _wrap(draw, body, font, 1100)
    step = 39 if font.size == 28 else 44
    if y + len(lines) * step > 618:
        raise ValueError('Metin görsele sığmıyor; otomatik kesilmedi. Metni kısaltın.')
    for line in lines:
        draw.text((72, y), line, font=font, fill='#202d2c')
        y += step
    draw.line((72, 646, 1208, 646), fill='#c8bca9', width=1)
    draw.text((72, 669), 'Özgün açıklama • Gerçek uygulama ekranı değildir', font=_font(18), fill='#655b4d')
    im.save(path, format='PNG', optimize=True)


def build_media(draft: dict, out_dir: Path, kind: str) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if kind == 'audio' and draft.get('cloud_tts_allowed') is not True:
        raise ValueError('Seslendirme için seçilen metnin bulut servisine gönderilmesine izin gerekli.')
    if kind == 'audio':
        return _audio(draft, out_dir)
    if kind == 'video':
        return _video(draft, out_dir)
    if kind != 'card':
        raise ValueError('Bu medya türü henüz desteklenmiyor.')
    main = str(draft.get('main', '')).strip()
    title = str(draft.get('title') or 'KarıncaAI notu')
    _card(title, main, out_dir / 'card.png')
    alt = (title + '. ' + main)[:1000]
    return {'kind': kind, 'files': [{'name': 'card.png', 'kind': 'image', 'mime': 'image/png'}], 'alt_text': alt,
            'rights': None, 'provenance': 'local_original_explanatory_card',
            'warnings': ['Bu yerelde üretilen özgün açıklayıcı karttır; kaynak tweet görseli değildir.', 'Bu kart gerçek uygulama demosu veya ekran kaydı değildir.', 'Hak beyanı kullanıcı yüklemesine ait değildir; kullanımdan önce insan incelemesi gerekir.']}


def _video(draft, out_dir):
    scenes = draft.get('scene_texts', [])
    if not isinstance(scenes, list) or not 3 <= len(scenes) <= 5 or any(not isinstance(s, str) or not s.strip() or len(s) > 220 for s in scenes):
        raise ValueError('Video için 3–5 kısa sahne gerekli; her sahne en fazla 220 karakter.')
    if psutil.virtual_memory().available < 768 * 1024**2:
        raise ValueError('Video için kullanılabilir RAM 768 MiB altına düştü. Diğer işleri kapatıp yeniden deneyin.')
    if shutil.disk_usage(out_dir).free < 512 * 1024**2:
        raise ValueError('Video için diskte en az 512 MiB boş alan gerekli.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('FFmpeg bulunamadı; video üretilmedi.')
    blocks, vtt = [], ['WEBVTT\n']
    for i, scene in enumerate(scenes):
        _card(draft.get('title') or 'KarıncaAI notu', scene, out_dir / f'scene-{i+1:02d}.png', f'SAHNE {i+1} / {len(scenes)}')
        start, end = i*4, (i+1)*4
        a, b = f'00:00:{start:02d}', f'00:00:{end:02d}'
        blocks.append(f'{i+1}\n{a},000 --> {b},000\n{scene.strip()}\n')
        vtt.append(f'{a}.000 --> {b}.000\n{scene.strip()}\n')
    (out_dir / 'captions.tr.srt').write_text('\n'.join(blocks), encoding='utf-8')
    (out_dir / 'captions.tr.vtt').write_text('\n'.join(vtt), encoding='utf-8')
    proc = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-framerate', '1/4', '-i', 'scene-%02d.png', '-t', str(len(scenes)*4), '-r', '24', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-threads', '2', '-an', '-movflags', '+faststart', 'video.mp4'], cwd=out_dir, capture_output=True, timeout=90)
    if proc.returncode:
        raise ValueError('Video kodlama başarısız; çıktı hazır sayılmadı.')
    alt = 'Özgün açıklayıcı sahneler: ' + ' '.join(scenes)
    return {'kind': 'video', 'files': [{'name': 'video.mp4', 'kind': 'video', 'mime': 'video/mp4'}, {'name': 'captions.tr.srt', 'kind': 'subtitle', 'mime': 'application/x-subrip', 'for_asset': 'video.mp4'}, {'name': 'captions.tr.vtt', 'kind': 'subtitle', 'mime': 'text/vtt', 'for_asset': 'video.mp4'}], 'alt_text': alt,
            'rights': None, 'provenance': 'local_original_explanatory_video',
            'warnings': ['Bu yerelde üretilen özgün açıklayıcı sahne videosudur; kaynak tweet görseli değildir.', 'Bu video gerçek uygulama demosu veya konuşma transkripsiyonu değildir.', 'Hak beyanı kullanıcı yüklemesine ait değildir; kullanımdan önce insan incelemesi gerekir.']}


def _audio(draft, out_dir):
    import edge_tts
    voice = draft.get('voice') or 'tr-TR-AhmetNeural'
    if voice not in {'tr-TR-AhmetNeural', 'tr-TR-EmelNeural'}:
        raise ValueError('Desteklenen Türkçe seslerden birini seçin.')
    script = str(draft.get('main', '')).strip()
    if not script or len(script) > 1500:
        raise ValueError('Ses metni 1–1500 karakter olmalı.')

    async def render():
        maker = edge_tts.SubMaker()
        with (out_dir/'voice.mp3').open('wb') as output:
            async for chunk in edge_tts.Communicate(script, voice, boundary='SentenceBoundary', connect_timeout=10, receive_timeout=30).stream():
                if chunk['type'] == 'audio':
                    output.write(chunk['data'])
                elif chunk['type'] in {'SentenceBoundary', 'WordBoundary'}:
                    maker.feed(chunk)
        srt = maker.get_srt()
        if '-->' not in srt:
            raise ValueError('Ses sağlayıcısı zaman damgası vermedi; altyazı tamamlanmadı.')
        (out_dir/'voice.tr.srt').write_text(srt, encoding='utf-8')
        import re
        vtt = re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', srt)
        (out_dir/'voice.tr.vtt').write_text('WEBVTT\n\n' + vtt, encoding='utf-8')

    try:
        asyncio.run(asyncio.wait_for(render(), timeout=60))
    except Exception as exc:
        raise ValueError('Ses servisine ulaşılamadı veya geçerli altyazı dönmedi; ses işi tamamlanmadı.') from exc
    return {'kind': 'audio', 'files': [{'name': 'voice.mp3', 'kind': 'audio', 'mime': 'audio/mpeg'}, {'name': 'voice.tr.srt', 'kind': 'subtitle', 'mime': 'application/x-subrip', 'for_asset': 'voice.mp3'}, {'name': 'voice.tr.vtt', 'kind': 'subtitle', 'mime': 'text/vtt', 'for_asset': 'voice.mp3'}], 'alt_text': script,
            'rights': None, 'provenance': 'synthetic_cloud_tts',
            'warnings': ['Microsoft Edge çevrimiçi sesi kullanıldı; ticari kullanım veya lisans hakkı otomatik garanti edilmez.', 'Bu sentetik seslendirmedir; gerçek bir kişinin sesinin taklidi değildir.']}
