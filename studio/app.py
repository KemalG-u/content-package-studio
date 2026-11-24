"""FastAPI application for the local-only Studio API."""
from __future__ import annotations

import hashlib
import io
import json
import base64
import re
import shutil
import subprocess
import uuid
import zipfile
from contextlib import nullcontext
from urllib.parse import urlparse
from pathlib import Path
from threading import Thread
from typing import Any
from queue import Full, Queue

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .generator import build_prompt, parse_generated_draft
from .editorial import EditorialError, approval_fingerprint, build_model_review_prompt, normalize_editorial, parse_model_review, validate_claim_map
from .media import build_media, prepare_photo
from . import model_worker
from .model_worker import MAX_PROMPT_CHARS, run_real_model
from .text_budget import measure_many
from .store import RevisionConflict, StoreError, StudioStore

LOCAL_HOSTS = {"127.0.0.1", "localhost"}
MAX_JSON_BYTES = 9 * 1024 * 1024
MAX_UPLOAD_BYTES = 6 * 1024 * 1024
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
# Fixed member timestamp: the same package content always yields the same SHA-256.
ZIP_MEMBER_TIME = (1980, 1, 1, 0, 0, 0)


def _require_prompt_within_limit(prompt: str) -> None:
    """Reject before a model budget slot is recorded; an oversized prompt never reaches the provider."""
    if len(prompt) > MAX_PROMPT_CHARS:
        limit, size = (f"{value:,}".replace(",", ".") for value in (MAX_PROMPT_CHARS, len(prompt)))
        raise StoreError(f"Model istemi {limit} karakter sınırını aşıyor ({size}); bütçe hakkı kullanılmadı. Proje kaynaklarını kısaltın veya ayırın.")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _safe_filename(name: Any) -> bool:
    return isinstance(name, str) and bool(name) and len(name) <= 255 and name not in {".", ".."} and "/" not in name and "\\" not in name and ":" not in name and Path(name).name == name


def _decode_upload(data_base64: Any) -> bytes:
    if not isinstance(data_base64, str) or not data_base64:
        raise StoreError("Dosya verisi geçersiz.")
    # Base64's maximum decoded length is known before allocation.
    if len(data_base64) > ((MAX_UPLOAD_BYTES + 2) // 3) * 4:
        raise StoreError("Dosya boyutu 6 MiB sınırını aşıyor.")
    try:
        raw = base64.b64decode(data_base64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise StoreError("Dosya verisi Base64 olarak çözülemedi.") from exc
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise StoreError("Dosya boyutu 6 MiB sınırını aşıyor.")
    return raw


def _magic_allows(raw: bytes, kind: str) -> bool:
    if kind == "photo":
        return raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n") or (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    if kind == "audio":
        return raw.startswith(b"ID3") or (len(raw) > 2 and raw[0] == 0xff and raw[1] & 0xe0 == 0xe0) or raw.startswith(b"RIFF") and raw[8:12] == b"WAVE" or raw.startswith(b"OggS") or raw.startswith(b"fLaC") or raw[4:8] == b"ftyp" or raw.startswith(b"\x1aE\xdf\xa3")
    if kind == "video":
        return raw[4:8] == b"ftyp" or raw.startswith(b"\x1aE\xdf\xa3") or raw.startswith(b"RIFF") and raw[8:12] == b"AVI "
    return False


def _probe_media(path: Path, kind: str) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise StoreError("FFprobe bulunamadı; dosya güvenli biçimde doğrulanamadı.")
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, timeout=15, check=False,
        )
        report = json.loads(completed.stdout.decode("utf-8"))
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoreError("Medya dosyası FFprobe doğrulamasını geçemedi.") from exc
    if completed.returncode or not isinstance(report, dict):
        raise StoreError("Medya dosyası FFprobe doğrulamasını geçemedi.")
    streams = report.get("streams", [])
    if not isinstance(streams, list) or not any(isinstance(stream, dict) and stream.get("codec_type") == kind for stream in streams):
        raise StoreError("Dosya beyan edilen medya türüyle eşleşmiyor.")
    try:
        duration = float(report.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        raise StoreError("Medya süresi doğrulanamadı.")
    if not 0 < duration <= 180:
        raise StoreError("Medya süresi en fazla 180 saniye olabilir.")
    format_name = str(report.get("format", {}).get("format_name", ""))
    if kind == "video":
        if "mov" in format_name or "mp4" in format_name:
            return "video/mp4"
        if "webm" in format_name or "matroska" in format_name:
            return "video/webm"
        if "avi" in format_name:
            return "video/x-msvideo"
    else:
        if "mp3" in format_name:
            return "audio/mpeg"
        if "wav" in format_name:
            return "audio/wav"
        if "ogg" in format_name:
            return "audio/ogg"
        if "flac" in format_name:
            return "audio/flac"
        if "mov" in format_name or "mp4" in format_name:
            return "audio/mp4"
        if "webm" in format_name or "matroska" in format_name:
            return "audio/webm"
    raise StoreError("Medya kapsayıcısı desteklenmiyor.")


def _copy_as_new(source: Path, destination_dir: Path, requested_name: str, forbidden_names: set[str] | None = None) -> Path:
    """Publish a staged file without ever replacing a sibling asset."""
    source, destination_dir = Path(source), Path(destination_dir)
    forbidden_names = forbidden_names or set()
    requested = Path(requested_name)
    token = uuid.uuid4().hex[:12]
    for counter in range(100):
        suffix = "" if counter == 0 else f"-{token}" if counter == 1 else f"-{token}-{counter}"
        name = requested.name if not suffix else f"{requested.stem}{suffix}{requested.suffix}"
        if name in forbidden_names:
            continue
        target = destination_dir / name
        try:
            output_handle = target.open("xb")
        except FileExistsError:
            continue
        try:
            with output_handle, source.open("rb") as input_handle:
                shutil.copyfileobj(input_handle, output_handle)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target
    raise StoreError("Yeni medya dosyası için çakışmasız ad ayrılamadı.")


def _publish_job_files(work_dir: Path, out_dir: Path, result: dict[str, Any], job_id: str, existing_names: set[str]) -> tuple[dict[str, Any], list[Path]]:
    """Copy a job's private outputs into revision storage without sibling overwrite."""
    if not isinstance(result, dict) or not isinstance(result.get("files"), list):
        raise StoreError("Medya işi geçerli varlık listesi vermedi.")
    published = {**result, "files": [dict(item) for item in result["files"]]}
    created: list[Path] = []
    name_map: dict[str, str] = {}
    reserved_names = set(existing_names)
    try:
        for item in published["files"]:
            name = item.get("name")
            if not _safe_filename(name):
                raise StoreError("Medya işi güvenli dosya adı vermedi.")
            source = work_dir / name
            destination = _copy_as_new(source, out_dir, name, reserved_names)
            created.append(destination)
            reserved_names.add(destination.name)
            name_map[name] = destination.name
            item["name"] = destination.name
        for item in published["files"]:
            if item.get("for_asset") in name_map:
                item["for_asset"] = name_map[item["for_asset"]]
        return published, created
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def create_app(db_path: Path | str = "studio.sqlite3", asset_root: Path | str = "studio-assets", run_jobs: bool = True, *, model_mission: str = 'legacy', read_only: bool = False) -> FastAPI:
    if read_only:
        run_jobs = False
    store = StudioStore(db_path, model_mission=model_mission, read_only=read_only)
    app = FastAPI(title="KarıncaAI Studio", docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.read_only = read_only
    app.state.asset_root = Path(asset_root).resolve()
    if not read_only:
        app.state.asset_root.mkdir(parents=True, exist_ok=True)
    job_queue: Queue[tuple[Any, dict[str, Any], Any]] = Queue(maxsize=1)
    app.state.job_queue = job_queue


    def process_generation(job: dict[str, Any], instruction: str) -> None:
        started = False
        try:
            store.start_job(job["id"])
            # Queue creation stays backward-compatible; actual model work is
            # fail-closed until a progressive brief is complete.
            store.require_editorial_ready(job["draft_id"], job["revision"], for_generation=True)
            draft = store.get_draft(job["draft_id"])
            source = store.source_for_draft(job["draft_id"])
            prompt = build_prompt(draft, source, instruction)
            _require_prompt_within_limit(prompt)
            store.start_model_run(job["id"], "generate")
            started = True
            result = run_real_model(prompt, app.state.asset_root / "model-worker.jsonl")
            generated = parse_generated_draft(result["text"])
            store.save_generated_proposal(job, generated)
            store.finish_model_run(job["id"], result.get("response_id"), result.get("output_sha256"), True)
            store.set_job(job["id"], "succeeded", "response_id=" + str(result.get("response_id") or "yok"))
        except RevisionConflict:
            if started:
                store.finish_model_run(job["id"], None, None, False)
            store.set_job(job['id'], 'failed', 'İş sırasında taslak değişti; eski çıktı uygulanmadı.')
            return
        except StoreError as exc:
            if started:
                store.finish_model_run(job["id"], None, None, False)
            store.set_job(job["id"], "failed", str(exc))
        except Exception:
            if started:
                store.finish_model_run(job["id"], None, None, False)
            store.set_job(job["id"], "failed", "Üretim işi beklenmeyen bir hatayla sonlandı.")

    def process_model_review(job: dict[str, Any], _: Any) -> None:
        started = False
        try:
            store.start_job(job["id"])
            draft = store.get_draft(job["draft_id"])
            source = store.source_for_draft(job["draft_id"])
            prompt = build_model_review_prompt(draft, source)
            _require_prompt_within_limit(prompt)
            store.start_model_run(job["id"], "model_review")
            started = True
            result = run_real_model(prompt, app.state.asset_root / "model-worker.jsonl")
            review = parse_model_review(result["text"])
            store.save_model_review(job["draft_id"], job["revision"], review, expected_fingerprint=approval_fingerprint(draft))
            store.finish_model_run(job["id"], result.get("response_id"), result.get("output_sha256"), True)
            store.set_job(job["id"], "succeeded", "response_id=" + str(result.get("response_id") or "yok"))
        except RevisionConflict:
            if started:
                store.finish_model_run(job["id"], None, None, False)
            store.set_job(job["id"], "failed", "İnceleme sırasında taslak değişti; eski eleştiri kaydedilmedi.")
        except (StoreError, ValueError) as exc:
            if started:
                store.finish_model_run(job["id"], None, None, False)
            store.set_job(job["id"], "failed", str(exc))
        except Exception:
            if started:
                store.finish_model_run(job["id"], None, None, False)
            store.set_job(job["id"], "failed", "Model eleştirisi beklenmeyen bir hatayla sonlandı.")

    def process_media(job: dict[str, Any], kind: str) -> None:
        work_dir: Path | None = None
        published_files: list[Path] = []
        try:
            store.start_job(job["id"])
            draft = store.get_draft(job["draft_id"])
            out_dir = app.state.asset_root / job["draft_id"] / f"r{job['revision']}"
            out_dir.mkdir(parents=True, exist_ok=True)
            work_dir = out_dir / f".job-{job['id']}"
            work_dir.mkdir()
            result = build_media(draft, work_dir, kind)
            existing_names = {asset.get("name") for asset in draft.get("assets", []) if asset.get("revision") == job["revision"]}
            result, published_files = _publish_job_files(work_dir, out_dir, result, job["id"], existing_names)
            store.register_media(job["draft_id"], job["revision"], out_dir, result)
            store.set_job(job["id"], "succeeded")
        except RevisionConflict:
            for path in published_files:
                path.unlink(missing_ok=True)
            store.set_job(job['id'], 'failed', 'Medya hazırlanırken taslak değişti; eski çıktı uygulanmadı.')
            return
        except (StoreError, ValueError) as exc:
            for path in published_files:
                path.unlink(missing_ok=True)
            store.set_job(job["id"], "failed", str(exc))
        except Exception:
            for path in published_files:
                path.unlink(missing_ok=True)
            store.set_job(job["id"], "failed", "Medya işi beklenmeyen bir hatayla sonlandı.")
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    def process_transcription(job: dict[str, Any], request_data: dict[str, Any]) -> None:
        work_dir: Path | None = None
        published_files: list[Path] = []
        try:
            store.start_job(job["id"])
            from .transcribe import transcribe_owned
            asset = store.get_asset(job["draft_id"], request_data["filename"])
            out_dir = app.state.asset_root / job["draft_id"] / f"r{job['revision']}"
            out_dir.mkdir(parents=True, exist_ok=True)
            work_dir = out_dir / f".job-{job['id']}"
            work_dir.mkdir()
            input_path = out_dir / asset["name"]
            result = transcribe_owned(input_path, work_dir, request_data["vps_allowed"])
            for file_info in result.get("files", []):
                if file_info.get("kind") == "subtitle":
                    file_info.setdefault("for_asset", asset["name"])
            result.update({"rights": asset["rights"], "provenance": "owned_local_transcription", "source_filename": asset["source_filename"]})
            draft = store.get_draft(job["draft_id"])
            existing_names = {entry.get("name") for entry in draft.get("assets", []) if entry.get("revision") == job["revision"]}
            result, published_files = _publish_job_files(work_dir, out_dir, result, job["id"], existing_names)
            store.register_media(job["draft_id"], job["revision"], out_dir, result)
            store.set_job(job["id"], "succeeded")
        except RevisionConflict:
            for path in published_files:
                path.unlink(missing_ok=True)
            store.set_job(job['id'], 'failed', 'Altyazı hazırlanırken taslak değişti; eski çıktı uygulanmadı.')
            return
        except (StoreError, ValueError) as exc:
            for path in published_files:
                path.unlink(missing_ok=True)
            store.set_job(job["id"], "failed", str(exc))
        except Exception:
            for path in published_files:
                path.unlink(missing_ok=True)
            store.set_job(job["id"], "failed", "Altyazı işi beklenmeyen bir hatayla sonlandı.")
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    def enqueue(processor: Any, job: dict[str, Any], argument: Any) -> None:
        if not run_jobs:
            return
        try:
            job_queue.put_nowait((processor, job, argument))
        except Full:
            store.set_job(job["id"], "failed", "İş kuyruğu dolu; aynı anda yalnız bir bekleyen iş kabul edilir.")

    def worker_loop() -> None:
        while True:
            processor, job, argument = job_queue.get()
            try:
                processor(job, argument)
            finally:
                job_queue.task_done()

    if run_jobs:
        Thread(target=worker_loop, daemon=True, name="studio-single-worker").start()

    @app.middleware("http")
    async def local_mutation_guard(request: Request, call_next):
        host = request.url.hostname
        if host not in LOCAL_HOSTS:
            return _error(403, "Yalnız localhost isteği kabul edilir.")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if read_only:
                return _error(403, "Studio salt-okunur inceleme modunda; değişiklik yapılmadı.")
            origin = request.headers.get("origin")
            parsed = urlparse(origin or "")
            expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if parsed.hostname not in LOCAL_HOSTS or origin != expected_origin:
                return _error(403, "Yerel uygulama kökeni doğrulanamadı.")
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return _error(415, "Yalnız JSON istekleri kabul edilir.")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_JSON_BYTES:
                    return _error(413, "İstek gövdesi sınırı aşıyor.")
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        messages = {404: "İstenen yerel kaynak bulunamadı.", 405: "Bu işlem desteklenmiyor."}
        return _error(exc.status_code, messages.get(exc.status_code, "İstek işlenemedi."))

    @app.exception_handler(RevisionConflict)
    async def revision_conflict_handler(_: Request, exc: RevisionConflict):
        return _error(409, str(exc))

    @app.exception_handler(StoreError)
    async def store_error_handler(_: Request, exc: StoreError):
        return _error(422, str(exc))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        worker = "disabled_read_only" if read_only else (("available" if (model_worker.HERMES_ROOT / "run_agent.py").is_file() else "unconfigured") if run_jobs else "test_harness_disabled")
        return {"ok": True, "x_writes_enabled": False, "local_only": True, "read_only": read_only, "model_worker": worker}

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return {"bookmarks": store.list_bookmarks(), "drafts": store.list_drafts(), "jobs": store.list_jobs(), "proposals": store.list_proposals(), "import_complete": store.import_complete(), "capabilities": {"x_writes": False, "local_uploads": not read_only, "transcription": run_jobs, "job_execution": run_jobs, "final_package_gate": not read_only}, "model_budget": store.model_budget_summary(), "read_only": read_only, "x_writes_enabled": False}

    @app.post('/api/drafts/{draft_id}/proposals/{proposal_id}/{action}')
    async def resolve_proposal(draft_id: str, proposal_id: str, action: str, request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise StoreError('Öneri isteği geçerli JSON değil; metin korunuyor.')
        if not isinstance(payload, dict) or set(payload) != {'revision'} or type(payload['revision']) is not int:
            raise StoreError('Öneri için güncel revizyon zorunludur.')
        if action == 'apply':
            return store.apply_proposal(draft_id,proposal_id,payload['revision'])
        if action == 'discard':
            return store.discard_proposal(draft_id,proposal_id,payload['revision'])
        raise StoreError('Öneri eylemi desteklenmiyor.')

    @app.post("/api/import")
    async def import_bookmarks(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("complete"), bool):
            raise StoreError("İçe aktarma gövdesi geçersiz.")
        return store.import_sources(payload.get("items"), payload.get("source"), payload["complete"])

    @app.post("/api/sources/manual", status_code=201)
    async def create_manual_source(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("Elle kaynak gövdesi geçerli JSON değil.") from exc
        return store.create_manual_source(payload)

    @app.delete("/api/sources/{source_id}")
    async def delete_manual_source(source_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("Kaynak silme isteği geçerli JSON değil.") from exc
        if not isinstance(payload, dict) or set(payload) != {"expected_url"}:
            raise StoreError("Kaynak silmek için görülen bağlantı zorunludur.")
        return store.delete_manual_source(source_id, payload["expected_url"])

    @app.post("/api/drafts/{draft_id}/sources")
    async def attach_project_source(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("Proje kaynağı gövdesi geçerli JSON değil.") from exc
        if not isinstance(payload, dict) or set(payload) != {"revision", "source_id"}:
            raise StoreError("Proje kaynağı için revizyon ve kaynak kimliği zorunludur.")
        return store.attach_project_source(draft_id, payload["revision"], payload["source_id"])

    @app.post('/api/measure')
    async def measure_copy(request: Request):
        try:
            payload = await request.json()
            return {'results': measure_many(payload['texts'])}
        except (ValueError, KeyError, TypeError):
            raise StoreError('Metin sayacı girdisi veya bağımlılığı geçersiz.')

    @app.post("/api/drafts", status_code=201)
    async def create_draft(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict):
            raise StoreError("Taslak gövdesi geçersiz.")
        return store.create_draft(payload.get("source_id"), payload.get("mode"), payload.get("objective"), payload.get("audience"))

    @app.put("/api/drafts/{draft_id}")
    async def update_draft(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or "revision" not in payload:
            raise StoreError("Taslak revizyonu zorunludur.")
        changes = {key: value for key, value in payload.items() if key != "revision"}
        return store.update_draft(draft_id, payload["revision"], changes)

    @app.get("/api/drafts/{draft_id}/history")
    def draft_history(draft_id: str, limit: int = 20) -> dict[str, Any]:
        return {"history": store.list_history(draft_id, limit)}

    @app.get("/api/drafts/{draft_id}/angles")
    def draft_angles(draft_id: str) -> dict[str, Any]:
        return store.list_angles(draft_id)

    @app.get("/api/drafts/{draft_id}/reviews")
    def draft_reviews(draft_id: str) -> dict[str, Any]:
        return store.editorial_reviews(draft_id)

    @app.post("/api/drafts/{draft_id}/editorial-review")
    async def editorial_review(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or set(payload) != {"revision"} or type(payload["revision"]) is not int:
            raise StoreError("Editoryal inceleme için geçerli revizyon zorunludur.")
        return store.save_deterministic_review(draft_id, payload["revision"])

    @app.post("/api/drafts/{draft_id}/model-review", status_code=202)
    async def model_review(draft_id: str, request: Request) -> dict[str, str]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or set(payload) != {"revision"} or type(payload["revision"]) is not int:
            raise StoreError("Model incelemesi için geçerli revizyon zorunludur.")
        job = store.create_job("model_review", draft_id, payload["revision"])
        enqueue(process_model_review, job, None)
        return {"job_id": job["id"]}

    @app.post("/api/drafts/{draft_id}/approvals/copy")
    async def approve_copy(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or set(payload) != {"revision", "fingerprint", "confirmed"} or payload["confirmed"] is not True or type(payload["revision"]) is not int:
            raise StoreError("Açık insan metin onayı için revizyon, parmak izi ve confirmed:true zorunludur.")
        return store.approve_copy(draft_id, payload["revision"], payload["fingerprint"])

    @app.post("/api/drafts/{draft_id}/restore")
    async def restore_draft(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or set(payload) != {"revision", "restore_revision"}:
            raise StoreError("Geri alma için iki revizyon zorunludur.")
        return store.restore_draft(draft_id, payload["revision"], payload["restore_revision"])

    @app.post("/api/drafts/{draft_id}/review")
    async def review_draft(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or "revision" not in payload:
            raise StoreError("Taslak revizyonu zorunludur.")
        return store.review_draft(draft_id, payload["revision"])

    @app.post("/api/drafts/{draft_id}/primary-media")
    async def select_primary_media(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or set(payload) != {"revision", "filename"}:
            raise StoreError("Birincil medya için revizyon ve dosya adı zorunludur.")
        return store.select_primary_media(draft_id, payload["revision"], payload["filename"])

    @app.post("/api/drafts/{draft_id}/generate", status_code=202)
    async def generate_draft(draft_id: str, request: Request) -> dict[str, str]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("revision"), int) or not isinstance(payload.get("instruction"), str):
            raise StoreError("Üretim gövdesi geçersiz.")
        if len(payload["instruction"]) > 4_000:
            raise StoreError("Üretim talimatı sınırı aşıyor.")
        job = store.create_job("generate", draft_id, payload["revision"])
        enqueue(process_generation, job, payload["instruction"])
        return {"job_id": job["id"]}

    @app.post("/api/drafts/{draft_id}/media", status_code=202)
    async def create_media(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        if not isinstance(payload, dict) or "revision" not in payload:
            raise StoreError("Taslak revizyonu zorunludur.")
        if payload.get("kind") not in {"card", "video", "audio"}:
            raise StoreError("Medya türü desteklenmiyor.")
        draft = store.get_draft(draft_id)
        if draft["revision"] != payload["revision"]:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        if not draft["main"].strip():
            raise StoreError("Medya için ana metin gereklidir.")
        if payload["kind"] == "audio" and not draft["cloud_tts_allowed"]:
            raise StoreError("Bulut ses üretimi için açık izin gereklidir.")
        job = store.create_job("media", draft_id, payload["revision"])
        enqueue(process_media, job, payload["kind"])
        return {"job_id": job["id"]}

    @app.post("/api/drafts/{draft_id}/upload", status_code=201)
    async def upload_local_media(draft_id: str, request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        required = {"revision", "kind", "filename", "data_base64", "rights", "alt_text"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise StoreError("Dosya yükleme gövdesi geçersiz.")
        revision, kind, filename, rights, alt_text = (payload["revision"], payload["kind"], payload["filename"], payload["rights"], payload["alt_text"])
        if not isinstance(revision, int) or kind not in {"photo", "audio", "video"} or not _safe_filename(filename):
            raise StoreError("Dosya türü, adı veya taslak revizyonu geçersiz.")
        if rights not in {"owned", "licensed"}:
            raise StoreError("Dosya için sahiplik veya lisans beyanı gerekli.")
        if not isinstance(alt_text, str) or not alt_text.strip() or len(alt_text) > 1_000:
            raise StoreError("Alt metin 1–1000 karakter olmalı.")
        draft = store.get_draft(draft_id)
        if draft["revision"] != revision:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        raw = _decode_upload(payload["data_base64"])
        if not _magic_allows(raw, kind):
            raise StoreError("Dosya imzası beyan edilen medya türüyle eşleşmiyor.")
        out_dir = app.state.asset_root / draft_id / f"r{revision}"
        out_dir.mkdir(parents=True, exist_ok=True)
        existing_names = {asset.get("name") for asset in draft.get("assets", []) if asset.get("revision") == revision}
        staging_token = uuid.uuid4().hex
        staged_input = out_dir / f".upload-{staging_token}.source"
        staged_output: Path | None = None
        output_path: Path | None = None
        if kind == "photo":
            requested_name = f"{Path(filename).stem}.jpg"
            if not _safe_filename(requested_name):
                raise StoreError("Fotoğraf çıktı adı güvenli değil.")
            staged_output = out_dir / f".upload-{staging_token}.jpg"
            try:
                staged_input.write_bytes(raw)
                prepare_photo(staged_input, staged_output, rights, alt_text)
                output_path = _copy_as_new(staged_output, out_dir, requested_name, existing_names)
            except (OSError, ValueError) as exc:
                raise StoreError("Fotoğraf gerçek olarak çözülemedi veya işlenemedi.") from exc
            finally:
                staged_input.unlink(missing_ok=True)
                if staged_output is not None:
                    staged_output.unlink(missing_ok=True)
            media_kind, mime = "photo", "image/jpeg"
        else:
            try:
                staged_input.write_bytes(raw)
                mime = _probe_media(staged_input, kind)
                output_path = _copy_as_new(staged_input, out_dir, filename, existing_names)
            except OSError as exc:
                if output_path is not None:
                    output_path.unlink(missing_ok=True)
                raise StoreError("Yerel medya dosyası kaydedilemedi.") from exc
            except StoreError:
                if output_path is not None:
                    output_path.unlink(missing_ok=True)
                raise
            finally:
                staged_input.unlink(missing_ok=True)
            media_kind = kind
        assert output_path is not None
        output_name = output_path.name
        try:
            saved = store.register_media(draft_id, revision, out_dir, {
                "kind": media_kind,
                "files": [{"name": output_name, "kind": media_kind, "mime": mime}],
                "rights": rights,
                "provenance": "local_upload",
                "source_filename": filename,
                "alt_text": alt_text,
                "warnings": ["Yerel kullanıcı yüklemesi; hak ve açıklama beyanı paket manifestine kaydedildi."],
            })
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        asset = next(asset for asset in reversed(saved["assets"]) if asset["name"] == output_name and asset["revision"] == revision)
        return {"asset": asset}

    @app.post("/api/drafts/{draft_id}/transcribe", status_code=202)
    async def transcribe_local_media(draft_id: str, request: Request) -> dict[str, str]:
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError("JSON gövdesi geçersiz.") from exc
        required = {"revision", "filename", "vps_allowed"}
        if not isinstance(payload, dict) or set(payload) != required or not isinstance(payload["revision"], int) or not _safe_filename(payload["filename"]) or payload["vps_allowed"] is not True:
            raise StoreError("Altyazı isteği geçersiz veya VPS izni eksik.")
        draft = store.get_draft(draft_id)
        if draft["revision"] != payload["revision"]:
            raise RevisionConflict("Taslak başka bir düzenleme ile değişti.")
        asset = store.get_asset(draft_id, payload["filename"])
        if asset.get("revision") != payload["revision"] or asset.get("kind") not in {"audio", "video"} or asset.get("rights") not in {"owned", "licensed"} or asset.get("provenance") != "local_upload":
            raise StoreError("Yalnız daha önce yerel olarak yüklenmiş ses veya video altyazıya gönderilebilir.")
        job = store.create_job("transcribe", draft_id, payload["revision"])
        enqueue(process_transcription, job, payload)
        return {"job_id": job["id"]}

    def serve_asset(draft_id: str, filename: str, asset: dict[str, Any], immutable: bool = False):
        path = (app.state.asset_root / draft_id / f"r{asset['revision']}" / filename).resolve()
        root = app.state.asset_root.resolve()
        if path.parent != (root / draft_id / f"r{asset['revision']}").resolve() or not path.is_file():
            raise StoreError("Varlık dosyası güvenli biçimde bulunamadı.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise StoreError("Varlık bütünlük denetimini geçemedi.")
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if immutable else {}
        return FileResponse(path, media_type=asset["mime"], filename=filename, headers=headers)

    @app.get("/api/assets/{draft_id}/r/{revision}/{sha256}/{filename}")
    def get_immutable_asset(draft_id: str, revision: int, sha256: str, filename: str):
        asset = store.get_asset(draft_id, filename, revision, sha256)
        return serve_asset(draft_id, filename, asset, True)

    @app.get("/api/assets/{draft_id}/{filename}")
    def get_asset(draft_id: str, filename: str, revision: int | None = None, sha256: str | None = None):
        if (revision is None) != (sha256 is None):
            raise StoreError("Immutable varlık sorgusu revizyon ve SHA-256 birlikte ister.")
        asset = store.get_asset(draft_id, filename, revision, sha256)
        return serve_asset(draft_id, filename, asset, revision is not None)

    def pending_proposal_count(draft_id: str) -> int:
        return sum(1 for item in store.list_proposals() if item["draft_id"] == draft_id and item["status"] == "pending")

    def final_gate_for(draft: dict[str, Any], previewed_fingerprint: str) -> dict[str, Any]:
        """Server-side final-save gate; the UI gate is only a convenience."""
        if draft.get("copy_approved") is not True:
            raise StoreError("Son yerel kayıt için güncel insan metin onayı gerekir; ara inceleme paketi ayrıca kaydedilebilir.")
        approvals = draft.get("approvals")
        copy = approvals.get("copy") if isinstance(approvals, dict) else None
        if not isinstance(copy, dict) or copy.get("source") != "explicit_human_confirmation" or not isinstance(copy.get("fingerprint"), str):
            raise StoreError("Metin onayı kaydı eksik veya geçersiz; son önizlemeden yeniden onaylayın.")
        current = approval_fingerprint(draft)
        if copy["fingerprint"] != current:
            raise RevisionConflict("Metin onayı bu sürüme ait değil; son önizlemeyi açıp yeniden onaylayın.")
        if previewed_fingerprint != current:
            raise RevisionConflict("Önizlediğiniz sürüm değişti; son önizlemeyi yeniden açın.")
        if pending_proposal_count(draft["id"]):
            raise RevisionConflict("Bekleyen model önerisi var; önce öneriyi düzenlemeye alın veya vazgeçin.")
        return {
            "approval_fingerprint": current, "copy_approved_at": copy.get("at"),
            "human_preview_confirmation": True, "pending_proposals": 0,
            "note": "Sunucu insanın önizlemeye gerçekten baktığını kanıtlayamaz; açık insan beyanı ve parmak izi eşleşmesi kaydedilir. Yayına hazır anlamına gelmez.",
        }

    @app.get("/api/drafts/{draft_id}/export")
    def export_draft(draft_id: str):
        data, manifest = build_package(draft_id)
        return Response(content=data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{draft_id}-r{manifest["revision"]}.zip"'})

    def build_package(draft_id: str, purpose: str = "interim_review", final_gate: dict[str, Any] | None = None) -> tuple[bytes, dict[str, Any]]:
        """Build the ZIP bytes; not a route, so no query parameter can select the final purpose."""
        if purpose not in {"interim_review", "final_local"} or (purpose == "final_local") != (final_gate is not None):
            raise StoreError("Paket amacı geçersiz.")
        draft = store.get_draft(draft_id)
        if not draft["main"].strip():
            raise StoreError("Boş ana metinle paket dışa aktarılamaz.")
        try:
            text_counts = measure_many([draft["main"], draft["reply"]])
        except ValueError as exc:
            raise StoreError("X metin sayacı doğrulanamadı; ZIP metin inceleme durumu üretilemedi.") from exc
        text_labels = ("main.txt", "reply.txt")
        text_review_required = not draft["copy_approved"]
        text_warnings: list[str] = [
            "Metin onayı yalnız main.txt ve reply.txt içindir; medya, birincil medya, haklar veya paket bütünlüğü için yayına hazır anlamına gelmez.",
            "Paket ayrı insan incelemesi gerektirir; bu ZIP X'e yayın yapmaz.",
        ]
        for label, text, count in zip(text_labels, (draft["main"], draft["reply"]), text_counts):
            if not text:
                continue
            if not count.get("valid") or count.get("weighted_length", 281) > 280:
                text_review_required = True
                text_warnings.append(f"UYARI: {label} X weighted {count.get('weighted_length', '?')}/280; taslak dışa aktarıldı, metin onayı verilemez.")
        source = store.source_for_draft(draft_id)
        project_sources = source.get("project_sources") if isinstance(source.get("project_sources"), list) else [source]
        source_lines = ["# İçerik projesi kaynakları", ""]
        for index, item in enumerate(project_sources, 1):
            source_lines.extend([
                f"## {index}. {item.get('title') or 'Başlıksız kaynak'}",
                f"- Tür: {item.get('kind') or 'x'}",
                f"- URL: {item.get('url') or 'Kayıt yok'}",
                f"- Gözlem: {item.get('observed_at') or 'Kayıt yok'}",
                f"- Kanıt notu: {item.get('evidence_note') or 'Not yok'}",
                "",
            ])
        entries: dict[str, bytes] = {
            "main.txt": draft["main"].encode("utf-8"),
            "reply.txt": draft["reply"].encode("utf-8"),
            "sources.md": "\n".join(source_lines).encode("utf-8"),
            "project-sources.json": json.dumps(project_sources, ensure_ascii=False, indent=2).encode("utf-8"),
        }
        evidence = draft.get("source_evidence")
        if evidence is not None:
            entries["source-evidence.json"] = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
            evidence_note = f"\n## Taslağın kayıtlı araştırma kopyası\n\nKontrol tarihi: {evidence['checked_at']}\nErişim kapsamı: {evidence['full_text_status']}; doğruluk onayı değildir.\n\n"
            evidence_note += "\n".join(f"- {item['url']} — {item['relation']}" for item in evidence["linked_primary_sources"])
            evidence_note += f"\n\nMedya hak notu: {evidence['rights_media_note']}\nDoğrulanmamış iddialar source-evidence.json içinde ayrıca kayıtlıdır.\n"
            entries["sources.md"] += evidence_note.encode("utf-8")
        current_assets = [asset for asset in draft["assets"] if asset.get("revision") == draft["revision"] and asset.get("kind") != "alt"]
        editorial = normalize_editorial(draft)
        claim_map = editorial["claim_map"]
        if claim_map is not None:
            try:
                validate_claim_map(claim_map, editorial["reader_brief"], draft.get("assets", []), allowed_urls=store.allowed_source_urls(source))
            except EditorialError as exc:
                raise StoreError("Paket claim map bütünlük denetimini geçemedi: " + str(exc)) from exc
        else:
            text_warnings.append("UYARI: Claim map henüz kaydedilmedi; paket insan editoryal incelemesi gerektirir.")
        if editorial["reader_brief"].get("selected_angle") is None:
            text_warnings.append("UYARI: Seçilmiş editoryal açı yok; paket insan editoryal incelemesi gerektirir.")
        audit = editorial["audit"] or {"status": "missing", "semantic_truth_note": "Deterministik veya model incelemesi anlamsal doğruluğu tek başına kanıtlamaz."}
        entries.update({
            "reader-brief.json": json.dumps(editorial["reader_brief"], ensure_ascii=False, indent=2).encode("utf-8"),
            "claim-map.json": json.dumps(claim_map, ensure_ascii=False, indent=2).encode("utf-8"),
            "deterministic-review.json": json.dumps(editorial["reviews"]["deterministic"], ensure_ascii=False, indent=2).encode("utf-8"),
            "model-review.json": json.dumps(editorial["reviews"]["model"], ensure_ascii=False, indent=2).encode("utf-8"),
            "audit.json": json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8"),
            "rights.json": json.dumps({"rights_note": editorial["rights_note"], "assets": [{"name": asset["name"], "rights": asset.get("rights"), "provenance": asset.get("provenance"), "source_filename": asset.get("source_filename")} for asset in current_assets]}, ensure_ascii=False, indent=2).encode("utf-8"),
        })
        evidence_pairs = {(item.get("asset_name"), item.get("sha256")) for item in (claim_map or {}).get("evidence", []) if item.get("kind") == "local_artifact"}
        evidence_assets = [asset for asset in draft.get("assets", []) if (asset.get("name"), asset.get("sha256")) in evidence_pairs]
        export_assets = current_assets + [asset for asset in evidence_assets if asset not in current_assets]
        entries["rights.json"] = json.dumps({"rights_note": editorial["rights_note"], "assets": [{"name": asset["name"], "revision": asset["revision"], "sha256": asset["sha256"], "rights": asset.get("rights"), "provenance": asset.get("provenance"), "source_filename": asset.get("source_filename")} for asset in export_assets]}, ensure_ascii=False, indent=2).encode("utf-8")
        editorial_review_required = claim_map is None or editorial["reader_brief"].get("selected_angle") is None or editorial["audit"] is None
        primary_media = draft.get("primary_media")
        primary_asset = None
        if primary_media:
            primary_asset = next((asset for asset in current_assets if asset.get("name") == primary_media.get("name") and asset.get("revision") == primary_media.get("revision") and asset.get("sha256") == primary_media.get("sha256") and asset.get("kind") in {"image", "photo", "video"}), None)
            primary_media = None if primary_asset is None else {"name": primary_asset["name"], "revision": primary_asset["revision"], "sha256": primary_asset["sha256"]}
        package_names: dict[tuple[Any, Any, Any], str] = {}
        for asset in export_assets:
            name = asset["name"]
            path = (app.state.asset_root / draft_id / f"r{asset['revision']}" / name).resolve()
            expected_parent = (app.state.asset_root.resolve() / draft_id / f"r{asset['revision']}").resolve()
            if path.parent != expected_parent or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
                raise StoreError("Dışa aktarılacak varlık bütünlük denetimini geçemedi.")
            package_name = name if asset in current_assets else f"evidence/r{asset['revision']}/{name}"
            reserved = {key.casefold() for key in entries} | {'manifest.json', 'alt-text.txt'}
            if package_name.casefold() in reserved:
                package_name = f"media/r{asset['revision']}/{name}"
            if package_name.casefold() in {key.casefold() for key in entries}:
                raise StoreError('Paket üye adı çakıştı; hiçbir dosyanın üzerine yazılmadı.')
            package_names[(asset["name"], asset["revision"], asset["sha256"])] = package_name
            entries[package_name] = path.read_bytes()
        if primary_asset is not None:
            entries["alt-text.txt"] = primary_asset.get("alt_text", "").encode("utf-8")
        manifest_assets = [{
            "name": asset["name"], "kind": asset["kind"], "mime": asset["mime"],
            "sha256": asset["sha256"], "size": asset["size"], "rights": asset.get("rights"),
            "provenance": asset.get("provenance"), "source_filename": asset.get("source_filename"), "for_asset": asset.get("for_asset"),
            "alt_text": asset.get("alt_text", ""), "warnings": asset.get("warnings", []),
            "package_name": package_names[(asset["name"], asset["revision"], asset["sha256"])],
        } for asset in export_assets]
        manifest = {"draft_id": draft_id, "revision": draft["revision"], "x_writes_enabled": False,
                    "publication_ready": False, "review_required": text_review_required or editorial_review_required,
                    "text_review_required": text_review_required, "package_review_required": True,
                    "approval_scope": "text_only",
                    "text_lengths": {label: count for label, count in zip(text_labels, text_counts)},
                    "primary_media": primary_media, "assets": manifest_assets,
                    "warnings": text_warnings + [warning for asset in manifest_assets for warning in asset["warnings"]],
                    "approval_fingerprint": approval_fingerprint(draft), "approvals": editorial["approvals"],
                    "package_purpose": purpose,
                    "files": {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}}
        if final_gate is not None:
            manifest["final_gate"] = final_gate
        entries["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                member = zipfile.ZipInfo(name, date_time=ZIP_MEMBER_TIME)
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = 0o600 << 16
                archive.writestr(member, data)
        return bundle.getvalue(), manifest

    @app.post('/api/drafts/{draft_id}/save-package')
    async def save_package(draft_id: str, request: Request):
        try:
            payload = await request.json()
        except Exception as exc:
            raise StoreError('Paket isteği geçersiz.') from exc
        final = isinstance(payload, dict) and set(payload) == {'revision', 'fingerprint', 'confirmed'}
        if not isinstance(payload, dict) or not (final or set(payload) == {'revision'}) or type(payload['revision']) is not int:
            raise StoreError('Paket isteği yalnız {revision} (ara inceleme paketi) veya {revision, fingerprint, confirmed:true} (son yerel paket) kabul eder.')
        if final and (payload['confirmed'] is not True or not isinstance(payload['fingerprint'], str) or not FINGERPRINT_RE.fullmatch(payload['fingerprint'])):
            raise StoreError('Son yerel paket için önizlenen sürümün parmak izi ve confirmed:true zorunludur.')
        # Only the final package reserves SQLite's writer slot. Hold it from the
        # first gate read through ZIP read-back: proposals, approvals and draft
        # revisions cannot commit in the check-to-file-write window.
        with store.final_package_lock() if final else nullcontext():
            before = store.get_draft(draft_id)
            if before['revision'] != payload['revision']:
                raise RevisionConflict('Taslak değişti; güncel sürümü açın.')
            gate = final_gate_for(before, payload['fingerprint']) if final else None
            data, manifest = build_package(draft_id, 'final_local' if final else 'interim_review', gate)
            if final and manifest['text_review_required']:
                raise StoreError('Son yerel paket için metin X sınırlarında ve insan onaylı olmalıdır.')
            if store.get_draft(draft_id) != before:
                raise RevisionConflict('Paket hazırlanırken taslak değişti.')
            if final and pending_proposal_count(draft_id):
                raise RevisionConflict('Paket hazırlanırken bekleyen model önerisi eklendi; önce öneriyi karara bağlayın.')
            digest = hashlib.sha256(data).hexdigest()
            output_dir = app.state.asset_root.parent / 'exports'
            output_dir.mkdir(parents=True, exist_ok=True)
            label = '-son-onayli' if final else ''
            output = output_dir / f'{draft_id}-r{before["revision"]}{label}-{digest[:12]}.zip'
            try:
                with output.open('xb') as handle:
                    handle.write(data)
            except FileExistsError:
                pass
            if hashlib.sha256(output.read_bytes()).hexdigest() != digest:
                raise StoreError('Kaydedilen paket bütünlük kontrolünü geçemedi.')
            return {'path': str(output.resolve()), 'sha256': digest, 'size': len(data), 'verified': True, 'publication_ready': False, 'purpose': manifest['package_purpose']}

    @app.get('/')
    @app.get('/index.html')
    def studio_index():
        # Revalidate the HTML on every load so a cached page cannot select an
        # incompatible JS revision after a local Studio update.
        return FileResponse(Path(__file__).resolve().parent / 'static' / 'index.html',
                            media_type='text/html', headers={'Cache-Control': 'no-cache, must-revalidate'})

    app.mount('/static', StaticFiles(directory=Path(__file__).resolve().parent / 'static'), name='static')
    app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent / 'static', html=True), name='studio')
    return app


def run_local() -> None:
    """Launch the service with an immutable loopback bind for local use."""
    import uvicorn
    root = Path(__file__).resolve().parent / 'data'
    uvicorn.run(create_app(root / 'studio.sqlite3', root / 'assets'), host="127.0.0.1", port=8767)


if __name__ == "__main__":
    run_local()
