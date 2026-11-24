# Architecture

Interpretation: this is a short map for readers, written from the code; it is not a specification.

## Pieces

| File | Role |
|---|---|
| `studio/store.py` | SQLite store (`studio.sqlite3`): sources (frozen, with optional source evidence), content projects (drafts), revisions, history, jobs, proposals, approvals, model-budget counters. Optimistic concurrency: writes carry the revision they were based on. |
| `studio/app.py` | FastAPI app, loopback only. Same-origin check on JSON writes, body and upload size limits, safe file names, hash-addressed asset URLs, single-worker job queue (`202` plus job id), ZIP export and the two package paths below. |
| `studio/editorial.py`, `studio/content_standard.py` | Editorial checks (claim map, quality gates), approval fingerprint, parsing of model reviews. |
| `studio/generator.py` | Builds the generation prompt from the frozen sources and the brief; parses the model reply into a draft. |
| `studio/model_worker.py` | Runs the model in an isolated subprocess (no tools, no fallback, bounded time and prompt size). Needs `HERMES_AGENT_ROOT`. |
| `studio/media.py`, `studio/transcribe.py` | Cards, optional video (ffmpeg), optional subtitle job to your own SSH host. |
| `studio/text_budget.py` | Weighted X length via Node and the pinned `twitter-text`. |
| `studio/cli.py` | Read-only JSON view of the database (`overview`, `project`, `source`). |
| `studio/launcher.py` | Starts the app on loopback; refuses to start if the port is taken, never kills another process. |
| `studio/static/` | Plain HTML/CSS/JS front end, no build step. |

## Rules the tests pin down

- A model result is a proposal; it never replaces the active text by itself.
- Editing text or media creates a new revision and invalidates earlier approvals; stale media is not packed.
- The interim review package may be unapproved; the final local package requires a current text approval whose fingerprint equals the previewed one, no pending proposal, and text within the X limit. `publication_ready` is always `false`.
- ZIP members have fixed timestamps, so the same content gives the same SHA-256.
- Paths from clients are never trusted; saving a package ignores any client path.
- A failed job is shown as failed; no placeholder draft or asset is created.
