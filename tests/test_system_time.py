import subprocess
import unittest
from datetime import datetime
from unittest.mock import patch

from backend.services.system_time import (
    parse_system_time_input,
    set_system_time,
)


class TestSystemTime(unittest.TestCase):
    def test_parses_browser_datetime_with_seconds(self):
        parsed = parse_system_time_input("2026-09-07T22:45:31")
        self.assertEqual(parsed, datetime(2026, 9, 7, 22, 45, 31))

    def test_parses_manual_datetime_without_seconds(self):
        parsed = parse_system_time_input("2026-09-07T22:45")
        self.assertEqual(parsed, datetime(2026, 9, 7, 22, 45))

    def test_rejects_invalid_datetime(self):
        with self.assertRaisesRegex(ValueError, "Ungültiges Zeitformat"):
            parse_system_time_input("07.09.2026 22:45")

    @patch("backend.services.system_time.run_system_time_command")
    @patch.dict("os.environ", {"CONTAINERIZED": "true"})
    def test_docker_sets_time_without_sudo(self, run_command):
        run_command.return_value = (True, "")

        result = set_system_time(datetime(2026, 9, 7, 22, 45, 31))

        self.assertEqual(result, (True, "2026-09-07 22:45:31"))
        run_command.assert_called_once_with(
            ["date", "--set", "2026-09-07 22:45:31"],
            "Systemzeit setzen (Docker)",
        )

    @patch("backend.services.system_time.run_system_time_command")
    @patch.dict("os.environ", {}, clear=True)
    def test_native_installation_falls_back_to_date(self, run_command):
        run_command.side_effect = [
            (True, ""),
            (False, "timedatectl fehlgeschlagen"),
            (True, ""),
            (True, ""),
        ]

        result = set_system_time(datetime(2026, 9, 7, 22, 45))

        self.assertEqual(result, (True, "2026-09-07 22:45:00"))
        self.assertEqual(
            run_command.call_args_list[2].args[0],
            ["sudo", "-n", "date", "--set", "2026-09-07 22:45:00"],
        )
        self.assertEqual(
            run_command.call_args_list[3].args[0],
            ["sudo", "-n", "hwclock", "--systohc"],
        )

    @patch("backend.services.system_time.subprocess.run")
    def test_permission_error_mentions_docker_capability(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["date"],
            returncode=1,
            stdout="",
            stderr="date: cannot set date: Operation not permitted",
        )

        with patch.dict("os.environ", {"CONTAINERIZED": "true"}):
            ok, message = set_system_time(datetime(2026, 9, 7, 22, 45))

        self.assertFalse(ok)
        self.assertIn("SYS_TIME", message)


if __name__ == "__main__":
    unittest.main()
