"""Workspace structure acceptance; browser checks are separately required."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent / 'static'

class WorkspaceContract(unittest.TestCase):
    def test_focused_workspace_exposes_real_stages_and_explicit_source_choice(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        for element in ['works-landing', 'works-list', 'work-flow', 'stage-mobile', 'new-source', 'new-work-from-source', 'source-pane', 'editor-pane', 'output-pane']:
            self.assertIn(f'id="{element}"', html)
        for stage in ['source', 'idea', 'edit', 'review', 'preview']:
            self.assertIn(f'data-stage-target="{stage}"', html)
        self.assertNotIn('data-layout="research"', html)
        self.assertNotIn('id="workspace-layout-controls"', html)

    def test_focused_workspace_css_has_one_task_surface_and_mobile_fallback(self):
        css = (ROOT / 'workspace.css').read_text(encoding='utf-8')
        for contract in ['.workbench[data-stage="source"] .editor-panel', '.workbench[data-stage="idea"] .source-rail', '.workbench[data-stage="preview"] .editor-panel', '.stage-mobile', '@media(max-width:420px)', 'max-width:980px', '.evidence-backdrop']:
            self.assertIn(contract, css)

    def test_content_projects_accept_explicit_multi_format_sources(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        script = (ROOT / 'app.js').read_text(encoding='utf-8')
        for element in ['manual-kind', 'attach-to-project', 'project-source-list']:
            self.assertIn(f'id="{element}"', html)
        for value in ['value="x"', 'value="web"', 'value="instagram"', 'value="youtube_transcript"']:
            self.assertIn(value, html)
        self.assertIn('/api/sources/manual', script)
        self.assertIn('/sources', script)
        self.assertIn('renderProjectSources', script)

    def test_model_review_cliche_question_uses_positive_ok_semantics(self):
        script = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn("cliches:'Klişe, boş vaat ve yapay açılıştan uzak mı?'", script)

    def test_narrow_mobile_layout_stacks_editor_fields_before_fractional_390px_edge(self):
        css = (ROOT / 'workspace.css').read_text(encoding='utf-8')
        self.assertIn("@media (max-width:420px)", css)
        self.assertIn(".form-grid{grid-template-columns:minmax(0,1fr);}", css)

    def test_focus_workspace_has_search_stages_review_and_optional_media(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        for element in ['work-flow','stage-mobile','works-list','draft-search','draft-status-filter','brief-panel','brief-problem','brief-benefit','brief-depth','brief-content-type','brief-provenance','brief-contribution','angle-options','preview-expand','review-dialog','editorial-review','evaluation-results','job-log']:
            self.assertIn(f'id="{element}"', html)
        self.assertIn('<details id="media-tools"', html)
        self.assertIn('<details id="motion-tools"', html)
        self.assertIn('<dialog id="review-dialog"', html)
        self.assertIn('X renderı değildir', html)

    def test_claim_editor_and_visible_dialog_close_are_present(self):
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        css=(ROOT/'workspace.css').read_text(encoding='utf-8')
        for element in ['claim-panel','claim-source','claim-original','claim-map-editor','rights-note']:
            self.assertIn(f'id="{element}"',html)
        self.assertIn('.icon-button { color:var(--ink);',css)

    def test_workspace_style_has_mobile_focus_and_reduced_motion(self):
        css = (ROOT / 'workspace.css').read_text(encoding='utf-8') if (ROOT / 'workspace.css').exists() else ''
        for contract in ['focus-visible','prefers-reduced-motion','320px','768px','minmax(0,','post-media']:
            self.assertIn(contract, css)
