# Third-party software

Nothing below is bundled in this repository; it is installed from `requirements.txt` and `studio/package-lock.json`, and keeps its own licence.

| Component | Use | Licence |
|---|---|---|
| FastAPI, Starlette, Uvicorn | web server | MIT / BSD-3-Clause / BSD-3-Clause |
| Pillow | image generation | MIT-CMU (HPND) |
| psutil | resource checks | BSD-3-Clause |
| twitter-text 3.1.0 (npm) | weighted X text length | Apache-2.0 |
| Hermes agent (not installed by this repo) | optional model worker | see its repository |
| ffmpeg / ffprobe (not installed by this repo) | optional video and media probing | LGPL/GPL depending on build |
