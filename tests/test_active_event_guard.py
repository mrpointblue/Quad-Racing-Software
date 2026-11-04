import ast
import unittest
from pathlib import Path


BASE_TEMPLATE = Path("backend/templates/base.html").read_text()
REGISTRATION_TEMPLATE = Path("backend/templates/registration_release.html").read_text()
CLASSES_TEMPLATE = Path("backend/templates/classes.html").read_text()
RUN_TEMPLATE = Path("backend/templates/run_detail.html").read_text()


def function_source(name: str) -> str:
    matches = []
    for path in [Path("backend/main.py"), *sorted(Path("backend/routers").glob("*.py"))]:
        source = path.read_text()
        for node in ast.parse(source).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                matches.append("\n".join(source.splitlines()[node.lineno - 1:node.end_lineno]))
    if len(matches) != 1:
        raise AssertionError(f"Funktion nicht eindeutig gefunden: {name} ({len(matches)} Treffer)")
    return matches[0]


class ActiveEventGuardTests(unittest.TestCase):
    def test_race_mutations_require_active_event(self):
        guarded_functions = [
            "ui_event_release_registration_group",
            "ui_event_revoke_registration_group",
            "ui_event_start_class",
            "ui_event_start_semifinal",
            "ui_event_start_final",
            "ui_event_start_run",
            "ui_event_reset_run",
            "ui_event_timekeeper_reset",
            "ui_event_repeat_run",
            "ui_event_set_false_start",
            "ui_event_save_run",
            "ui_event_confirm_run",
        ]

        for name in guarded_functions:
            with self.subTest(function=name):
                self.assertIn(
                    "ensure_event_is_active(active_event)",
                    function_source(name),
                )

    def test_inactive_event_message_uses_visible_ui_terms(self):
        self.assertIn("Diese Veranstaltung ist nicht aktiv.", BASE_TEMPLATE)
        self.assertIn("Aktiv setzen", BASE_TEMPLATE)

    def test_inactive_event_actions_are_hidden_or_disabled(self):
        self.assertIn(
            "active_event.active and not active_event.closed",
            REGISTRATION_TEMPLATE,
        )
        self.assertIn(
            "Klasse kann nur in der aktiven Veranstaltung gestartet werden.",
            CLASSES_TEMPLATE,
        )
        self.assertIn(
            "Veranstaltung nicht aktiv",
            RUN_TEMPLATE,
        )
        self.assertIn(
            "not active_event.active or result.repeat_is_fixed",
            RUN_TEMPLATE,
        )


if __name__ == "__main__":
    unittest.main()
