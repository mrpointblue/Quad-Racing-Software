import unittest
from pathlib import Path
from types import SimpleNamespace
try:
    from jinja2 import Environment
except ImportError:
    Environment = None

@unittest.skipIf(Environment is None, "Jinja2 required; installed by application CI")
class MonitorOrientationTests(unittest.TestCase):
    def test_initial_render_matches_live_orientation(self):
        source = Path("backend/templates/monitor.html").read_text()
        block = source.split("{% block body_class %}", 1)[1].split("{% endblock %}", 1)[0]
        template = Environment().from_string(block)
        for orientation in ("landscape", "portrait", "portrait_left", None):
            with self.subTest(orientation=orientation):
                classes = template.render(monitor_data=SimpleNamespace(display_setting=SimpleNamespace(
                    display_orientation=orientation, display_theme="dark"))).split()
                self.assertEqual("monitor-portrait" in classes, orientation in ("portrait", "portrait_left"))
                self.assertEqual("monitor-portrait-left" in classes, orientation == "portrait_left")
                self.assertEqual("monitor-landscape" in classes, orientation not in ("portrait", "portrait_left"))

    def test_picker_checks_exactly_one_choice(self):
        source = Path("backend/templates/monitor_output.html").read_text()
        block = source.split('<div class="pill-grid monitor-format-picker monitor-orientation-picker">', 1)[1].split("</div>", 1)[0]
        template = Environment().from_string(block)
        from html.parser import HTMLParser
        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.checked = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "input" and "checked" in attrs:
                    self.checked.append(attrs["value"])
        for orientation, expected in (("landscape", "landscape"), ("portrait", "portrait"), ("portrait_left", "portrait_left"), (None, "landscape")):
            parser = Inputs()
            parser.feed(template.render(setting=SimpleNamespace(display_orientation=orientation)))
            self.assertEqual(parser.checked, [expected])
