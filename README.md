> 🚧 **Status: active development.** It works for the author; interfaces may change.

# content-package-studio

A local, single-user web app that turns hand-collected sources (an X post, a web page, a YouTube transcript) into a reviewed draft post and a verifiable ZIP package: text, reply, alt text, source and rights notes, optional generated media, and a manifest with SHA-256 hashes. Nothing is ever published: the app has no X write path, and the health endpoint and every package manifest state `x_writes_enabled: false`.

**Language note:** the interface, the error messages and the editorial standard are in Turkish, because the content it prepares is for a Turkish-speaking audience. See [README.tr.md](README.tr.md).

## Why it exists

Drafts written with AI help tend to lose their evidence trail: which source said what, which text was approved by a person, which image belongs to which revision. This app keeps that trail in one SQLite file:

- sources are frozen when added, and a project may only use the sources explicitly linked to it;
- every edit is a new revision; changing text or media invalidates earlier approvals and marks old media as stale, so a stale file cannot end up in a package;
- the model's output is only ever a *proposal*; applying it to the active text is an explicit action, and a late result cannot overwrite what you are editing;
- the final local package is only produced after a human text approval tied to the exact preview fingerprint. The server cannot prove that a person really looked; it records the explicit confirmation and the fingerprint.

## What works

- Local FastAPI app on `127.0.0.1:8767`: sources, content projects, editor, preview, revision history, restore.
- Import and validation of sources, same-origin checks, upload size limits, path-traversal-safe asset storage.
- Weighted X text length using the pinned upstream `twitter-text` package (via Node), not a `len()` guess.
- Package export (ZIP with manifest and hashes), interim review package and final local package with a gate.
- Generated cards and (with ffmpeg) short videos; an optional subtitle job that sends *your own* file to an SSH host you control.
- A read-only JSON command line (`python -m studio.cli overview | project ID | source ID`) for agents or scripts.
- A test suite (about 150 tests) and a front-end flow test.

## Quick start

Requirements: Python 3.12, Node.js (for the text counter), optional ffmpeg/ffprobe (video, audio, uploads).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Linux/macOS: .venv/bin/python
npm ci --prefix studio
.venv/Scripts/python -m studio.launcher                      # opens http://127.0.0.1:8767
```

Run the tests:

```bash
.venv/Scripts/python -X utf8 -m unittest discover -s studio -t . -p "test_*.py"
node --test studio/test_frontend_flows.js
```

Useful launcher options: `--data-root DIR` (default `studio/data`), `--port`, `--no-browser`, `--read-only` (open an existing database for inspection; every write endpoint returns 403).

Environment variables:

| Variable | Purpose |
|---|---|
| `HERMES_AGENT_ROOT` | Folder of a Hermes agent checkout (the author's own agent runtime; not published here). Required for model generation (see limits). |
| `STUDIO_ASR_SSH_HOST` | SSH host alias for the optional subtitle job (default `vps`). |
| `STUDIO_REQUIRE_DRIVE` | Optional, Windows: refuse a data root outside this drive, e.g. `D:`. |

## Limits and unverified claims

- **Model generation only works with the author's setup.** Draft generation and model review call a Hermes agent in an isolated, tool-free subprocess with a fixed provider and model (`openai-codex` / `gpt-5.6-terra`, see `studio/model_worker.py`) and need a matching subscription. Without it, sources, editing, revisions, approvals, media and export still work, and generation reports an error instead of faking a draft. The test that exercises the real model is skipped unless `STUDIO_LIVE_MODEL=1`.
- **Tested on Windows only.** CI runs on Windows. Linux and macOS have not been tried; the subtitle worker (`asr_remote_worker.py`) is meant for a Linux host.
- Tests that need ffmpeg/ffprobe are skipped when those are missing; skipped is not passed.
- The editorial standard (`studio/content_standard.v1.json`) and the prompts are written in Turkish for one specific account and audience. Treat them as an example to replace, not a neutral default.
- The optional subtitle job uploads a media file you own to your SSH host; the generated narration uses an online text-to-speech service whose licence terms you must check yourself.
- No claim is made about reach, engagement or content quality. The app prepares and checks packages; it does not judge whether a post is good.
- Single user, loopback only. Do not expose it to a network.

More: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/CONTENT-STANDARD.md](docs/CONTENT-STANDARD.md).

## License

Code: Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). Dependencies keep their own licences; see [THIRD-PARTY.md](THIRD-PARTY.md).
