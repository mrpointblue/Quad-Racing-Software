"""Navigation fallback: render the actual header template for each run state."""
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from jinja2 import Environment, StrictUndefined
except ImportError:
    Environment = None


@unittest.skipIf(Environment is None, "Jinja2 required; installed by application CI")
class RunNavigationTests(unittest.TestCase):
    def render_header(self, confirmed, next_run):
        source = Path("backend/templates/run_detail.html").read_text()
        header = source.split('<div class="run-header-actions">', 1)[1].split('</div>', 1)[0]
        return Environment(undefined=StrictUndefined, autoescape=True).from_string(header).render(
            race_run=SimpleNamespace(id=10, confirmed=confirmed, race_class=2),
            active_event=SimpleNamespace(id=7),
            next_unconfirmed_run=next_run,
        )

    def test_confirmed_last_run_has_red_overview_link(self):
        html = self.render_header(True, None)
        self.assertIn("red-button next-run-button-prominent", html)
        self.assertIn("Durchlauf abgeschlossen – Zur Rennübersicht", html)
        self.assertIn('href="/ui/events/7/classes"', html)
        self.assertNotIn("Weiter zum nächsten Lauf", html)

    def test_confirmed_run_with_successor_keeps_green_navigation(self):
        successor = SimpleNamespace(id=11, round_label="Halbfinale", race_class=2, pairing_number=3, run_number=2)
        html = self.render_header(True, successor)
        self.assertIn('next-run-button next-run-button-prominent', html)
        self.assertIn('href="/ui/events/7/timekeeper/run/11"', html)
        self.assertNotIn("Durchlauf abgeschlossen", html)

    def test_unconfirmed_run_never_reports_completion(self):
        for next_run in (None, SimpleNamespace(id=10)):
            with self.subTest(next_run=next_run):
                self.assertNotIn("Durchlauf abgeschlossen", self.render_header(False, next_run))

    def test_unconfirmed_with_successor_retains_existing_link(self):
        successor = SimpleNamespace(id=11, round_label="Halbfinale", race_class=2, pairing_number=3, run_number=2)
        html = self.render_header(False, successor)
        self.assertIn("Nächster Lauf", html)
        self.assertNotIn("Durchlauf abgeschlossen", html)
        self.assertNotIn("next-run-button-prominent", html)
