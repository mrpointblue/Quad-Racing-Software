import unittest
from unittest.mock import patch

from backend.services.logo_client import LogoClient


SETTINGS = {
    "logo_enabled": "true",
    "logo_ip": "192.168.2.20",
    "logo_port": "502",
    "logo_timeout_ms": "1000",
    "logo_time_register_start": "0",
    "logo_output_coil_start": "8192",
    "logo_marker_coil_start": "8256",
    "logo_light_barrier_1_address": "0",
    "logo_light_barrier_2_address": "1",
    "logo_web_start_coil": "8262",
    "logo_web_reset_coil": "8264",
    "logo_pulse_duration_ms": "200",
}


class FakeResponse:
    def __init__(self, *, bits=None, registers=None, error=False):
        self.bits = bits or []
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error


class FakeModbusClient:
    def __init__(
        self,
        *,
        registers=(0, 1234, 0, 5678),
        outputs=(False, True, False),
        markers=(True, False, True, False, False, True),
        inputs=(True, False),
        input_error=False,
    ):
        self.registers = registers
        self.outputs = outputs
        self.markers = markers
        self.inputs = inputs
        self.input_error = input_error
        self.closed = False
        self.writes = []

    def read_holding_registers(self, *, address, count):
        return FakeResponse(registers=list(self.registers))

    def read_coils(self, *, address, count):
        bits = self.outputs if address == 8192 else self.markers
        return FakeResponse(bits=list(bits))

    def read_discrete_inputs(self, *, address, count):
        return FakeResponse(bits=list(self.inputs), error=self.input_error)

    def write_coil(self, address, value):
        self.writes.append((address, value))
        return FakeResponse()

    def close(self):
        self.closed = True


class TestLogoClient(unittest.TestCase):
    def build_client(self, fake):
        client = LogoClient(SETTINGS)
        client._connect = lambda: fake
        return client

    def test_disabled_status_has_complete_shape(self):
        status = LogoClient({"logo_enabled": "false"}).read_status()

        self.assertTrue(status["ok"])
        self.assertFalse(status["online"])
        self.assertIn("inputs", status)
        self.assertIn("light_barrier_fault_a", status["markers"])
        self.assertIn("light_barrier_fault_b", status["markers"])

    def test_status_converts_times_and_attributes_m6_fault_to_lane_a(self):
        fake = FakeModbusClient(inputs=(True, False))
        status = self.build_client(fake).read_status()

        self.assertTrue(status["ok"])
        self.assertTrue(status["online"])
        self.assertEqual(status["times"]["lane_a_ms"], 12340)
        self.assertEqual(status["times"]["lane_b_ms"], 56780)
        self.assertEqual(status["times"]["lane_a_display"], "00:12,34")
        self.assertEqual(status["times"]["lane_b_display"], "00:56,78")
        self.assertTrue(status["markers"]["light_barrier_fault"])
        self.assertTrue(status["markers"]["light_barrier_fault_a"])
        self.assertFalse(status["markers"]["light_barrier_fault_b"])
        self.assertTrue(fake.closed)

    def test_input_read_error_keeps_global_m6_fault_visible(self):
        fake = FakeModbusClient(input_error=True)
        status = self.build_client(fake).read_status()

        self.assertTrue(status["ok"])
        self.assertTrue(status["markers"]["light_barrier_fault"])
        self.assertFalse(status["markers"]["light_barrier_fault_a"])
        self.assertFalse(status["markers"]["light_barrier_fault_b"])
        self.assertNotEqual(status["inputs"]["read_error"], "")

    def test_invalid_time_response_returns_safe_offline_status(self):
        fake = FakeModbusClient(registers=())
        status = self.build_client(fake).read_status()

        self.assertFalse(status["ok"])
        self.assertFalse(status["online"])
        self.assertIn("zu wenige Register", status["message"])
        self.assertTrue(fake.closed)

    def test_web_start_pulses_configured_coil(self):
        fake = FakeModbusClient()
        client = self.build_client(fake)

        with patch("backend.services.logo_client.time.sleep") as sleep:
            result = client.pulse_web_start()

        self.assertTrue(result["ok"])
        self.assertEqual(fake.writes, [(8262, True), (8262, False)])
        sleep.assert_called_once_with(0.2)
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
