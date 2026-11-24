"""Minimal file-level contracts; behavioral UI flows live in test_frontend_flows.js."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
STATE_CSS = (ROOT / "static" / "state.css").read_text(encoding="utf-8")


class StudioFrontendContractTests(unittest.TestCase):
    def test_relative_same_origin_api_surface_includes_upload_and_asr(self):
        required = [
            "/api/state", "/api/import", "/api/drafts", "/generate", "/media", "/review", "/primary-media", "/export",
            "/upload", "/transcribe", "/api/assets/",
        ]
        for endpoint in required:
            self.assertIn(endpoint, JS)
        self.assertNotIn("localhost:8000", JS)
        self.assertNotIn("fakeData", JS)
        self.assertIn("credentials: 'same-origin'", JS)
        self.assertIn("Content-Type': 'application/json'", JS)

    def test_operable_upload_and_conflict_controls_have_stable_ids(self):
        required_ids = [
            "source-search", "create-draft", "generate-draft", "rewrite-draft", "main-copy", "reply-copy",
            "save-draft", "review-draft", "approve-copy", "export-zip", "loading-overlay", "asset-upload",
            "asset-rights", "asset-alt", "upload-asset", "upload-progress", "vps-asr-allowed", "job-status",
            "native-primary", "reload-server-draft",
        ]
        for control_id in required_ids:
            self.assertIn(f'id="{control_id}"', HTML)

    def test_small_screen_actions_do_not_overlay_editor(self):
        self.assertRegex(STATE_CSS, r"@media \(max-width: 400px\).*\.sticky-actions \{ position: static; \}")

    def test_safety_accessibility_and_preview_contract(self):
        self.assertIn("LOCAL · X’TE YAYINLANMADI", HTML)
        self.assertIn("X ana yüzeyi", HTML)
        self.assertIn("VPS ASR işçisine", HTML)
        self.assertIn("textContent", JS)
        self.assertNotIn("innerHTML", JS)
        self.assertIn("revizyon çakıştı", JS.lower())
        self.assertIn("track.kind = 'subtitles'", JS)
        self.assertIn("track.srclang = 'tr'", JS)
        self.assertIn("[hidden] { display: none !important; }", STATE_CSS)
        self.assertIn("@media(max-width:640px)", CSS)
        self.assertIn("prefers-reduced-motion", CSS)
        self.assertIn("Bahnschrift", CSS)
        self.assertIn("Segoe UI", CSS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
