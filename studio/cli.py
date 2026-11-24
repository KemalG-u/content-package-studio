"""Read-only, deterministic JSON surface for Hermes Studio operations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .store import StoreError, StudioStore

SCHEMA_VERSION = 1
DEFAULT_DB = Path(__file__).resolve().parent / "data" / "studio.sqlite3"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="KarıncaAI Studio salt-okunur JSON CLI")
    result.add_argument("--db", type=Path, default=DEFAULT_DB)
    result.add_argument("--model-mission", default="organic-growth-v1")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("overview", help="Kompakt kaynak/proje/iş özeti")
    project = commands.add_parser("project", help="Tek içerik projesinin tam dondurulmuş bağlamı")
    project.add_argument("draft_id")
    source = commands.add_parser("source", help="Tek kaynak ve bağlı proje kimlikleri")
    source.add_argument("source_id")
    return result


def _project_summary(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": draft["id"],
        "title": draft.get("title") or "",
        "revision": draft["revision"],
        "source_count": len(draft.get("project_sources") or []),
        "copy_approved": bool(draft.get("copy_approved")),
        "publication_ready": bool(draft.get("publication_ready")),
        "updated_at": draft.get("updated_at"),
    }


def _output(payload: dict[str, Any], stream: Any = sys.stdout) -> None:
    json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    stream.write("\n")


def _error(message: str, code: str = "not_found") -> int:
    _output({"schema_version": SCHEMA_VERSION, "error": {"code": code, "message": message}}, sys.stderr)
    return 2


def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.db.is_file():
        return _error(f"Studio veritabanı bulunamadı: {args.db}", "database_not_found")
    try:
        store = StudioStore(args.db, model_mission=args.model_mission, read_only=True)
        drafts = store.list_drafts()
        if args.command == "overview":
            sources = store.list_bookmarks()
            payload = {
                "schema_version": SCHEMA_VERSION,
                "command": "overview",
                "counts": {
                    "sources": len(sources),
                    "projects": len(drafts),
                    "jobs": len(store.list_jobs()),
                    "proposals": len(store.list_proposals()),
                },
                "model_budget": store.model_budget_summary(),
                "projects": [_project_summary(draft) for draft in drafts],
            }
        elif args.command == "project":
            payload = {"schema_version": SCHEMA_VERSION, "command": "project", "project": store.get_draft(args.draft_id)}
        else:
            source = next((item for item in store.list_bookmarks() if str(item["id"]) == str(args.source_id)), None)
            if source is None:
                return _error(f"Kaynak bulunamadı: {args.source_id}")
            project_ids = sorted(
                draft["id"]
                for draft in drafts
                if str(draft.get("source_id")) == str(args.source_id)
                or any(str(item.get("id")) == str(args.source_id) for item in draft.get("project_sources") or [])
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "command": "source",
                "source": source,
                "project_ids": project_ids,
            }
    except StoreError as exc:
        return _error(str(exc))
    _output(payload)
    return 0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # machine-readable JSON is always UTF-8, whatever the console code page
    raise SystemExit(run())


if __name__ == "__main__":
    main()
