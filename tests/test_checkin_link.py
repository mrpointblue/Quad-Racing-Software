"""Verify the actual check-in link expression, including IPv6 and query cleanup."""
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
try:
    from jinja2 import Environment
    from starlette.datastructures import URL
except ImportError:
    Environment = None

@unittest.skipIf(Environment is None, "Web dependencies installed by application CI")
class CheckinLinkTests(unittest.TestCase):
    def test_link_uses_current_host_and_checkin_port(self):
        source = Path("backend/templates/registration_list.html").read_text()
        expression = re.search(r'href="([^"]+)"[^>]*>\s*Check-in Terminal öffnen', source).group(1)
        template = Environment(autoescape=True).from_string(expression)
        for source_url, target in (
            ("http://192.168.2.10:8000/ui/events/7/registration?filter=open", "http://192.168.2.10:8010/checkin"),
            ("http://pi.local:8000/ui/events/7/registration", "http://pi.local:8010/checkin"),
            ("http://race:8000/ui/events/7/registration", "http://race:8010/checkin"),
            ("http://[2001:db8::10]:8000/ui/events/7/registration", "http://[2001:db8::10]:8010/checkin"),
            ("http://localhost:8000/ui/events/7/registration#open", "http://localhost:8010/checkin"),
            ("https://race.example/ui/events/7/registration", "http://race.example:8010/checkin"),
        ):
            with self.subTest(source_url=source_url):
                self.assertEqual(template.render(request=SimpleNamespace(url=URL(source_url))), target)
