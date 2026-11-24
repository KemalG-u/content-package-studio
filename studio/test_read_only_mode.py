"""The inspect mode must not mutate the Studio database."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from studio.app import create_app
from studio.store import StudioStore
from studio.test_backend import VALID_SOURCE


class ReadOnlyStudioTests(unittest.TestCase):
    def test_read_only_mode_serves_state_and_rejects_every_mutation_without_touching_db(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "studio.sqlite3"
            assets = root / "assets"
            store = StudioStore(db)
            store.import_sources([VALID_SOURCE], "manual_bookmark", False)
            store.create_draft(VALID_SOURCE["id"], "research", "Hedef", "Okur")
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            before_mtime = db.stat().st_mtime_ns

            app = create_app(db, assets, run_jobs=False, read_only=True)
            with TestClient(app, base_url="http://localhost") as client:
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 200, health.text)
                self.assertTrue(health.json()["read_only"])
                state = client.get("/api/state")
                self.assertEqual(state.status_code, 200, state.text)
                self.assertEqual(len(state.json()["drafts"]), 1)
                blocked = client.post("/api/import", json={"items": [VALID_SOURCE], "source": "manual_bookmark", "complete": False})
                self.assertEqual(blocked.status_code, 403, blocked.text)
                self.assertIn("salt-okunur", blocked.json()["error"].lower())

            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
            self.assertEqual(db.stat().st_mtime_ns, before_mtime)
            self.assertFalse(Path(str(db) + "-wal").exists())
            self.assertFalse(Path(str(db) + "-shm").exists())

    def test_launcher_exposes_explicit_read_only_flag(self) -> None:
        from studio.launcher import parser
        self.assertFalse(parser().parse_args([]).read_only)
        self.assertTrue(parser().parse_args(["--read-only"]).read_only)


if __name__ == "__main__":
    unittest.main()
