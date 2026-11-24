"""Deterministic, read-only JSON surface for agent operations."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from studio.store import StudioStore
from studio.test_backend import VALID_SOURCE


class StudioCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "studio.sqlite3"
        store = StudioStore(self.db, model_mission="organic-growth-v1")
        store.import_sources([VALID_SOURCE], "manual_bookmark", False)
        self.draft = store.create_draft(VALID_SOURCE["id"], "research", "Hedef", "Okur")
        self.before_hash = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.before_mtime = self.db.stat().st_mtime_ns

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "studio.cli", "--db", str(self.db), *args],
            cwd=Path(__file__).resolve().parent.parent,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def assert_unchanged(self) -> None:
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), self.before_hash)
        self.assertEqual(self.db.stat().st_mtime_ns, self.before_mtime)
        self.assertFalse(Path(str(self.db) + "-wal").exists())
        self.assertFalse(Path(str(self.db) + "-shm").exists())

    def test_overview_is_compact_versioned_json_and_does_not_touch_database(self) -> None:
        result = self.run_cli("overview")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["command"], "overview")
        self.assertEqual(body["counts"], {"sources": 1, "projects": 1, "jobs": 0, "proposals": 0})
        self.assertEqual(body["projects"][0]["id"], self.draft["id"])
        self.assertEqual(body["projects"][0]["source_count"], 1)
        self.assertNotIn("main", body["projects"][0], "overview should stay compact")
        self.assert_unchanged()

    def test_project_returns_only_requested_project_with_frozen_sources(self) -> None:
        result = self.run_cli("project", self.draft["id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["command"], "project")
        self.assertEqual(body["project"]["id"], self.draft["id"])
        self.assertEqual(body["project"]["objective"], "Hedef")
        self.assertEqual(len(body["project"]["project_sources"]), 1)
        self.assertEqual(body["project"]["project_sources"][0]["id"], VALID_SOURCE["id"])
        self.assert_unchanged()

    def test_source_returns_related_project_ids_without_other_project_payloads(self) -> None:
        result = self.run_cli("source", VALID_SOURCE["id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["command"], "source")
        self.assertEqual(body["source"]["id"], VALID_SOURCE["id"])
        self.assertEqual(body["project_ids"], [self.draft["id"]])
        self.assertNotIn("projects", body)
        self.assert_unchanged()

    def test_unknown_id_is_machine_readable_and_nonzero(self) -> None:
        result = self.run_cli("project", "missing")
        self.assertEqual(result.returncode, 2)
        body = json.loads(result.stderr)
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["error"]["code"], "not_found")
        self.assert_unchanged()


if __name__ == "__main__":
    unittest.main()
