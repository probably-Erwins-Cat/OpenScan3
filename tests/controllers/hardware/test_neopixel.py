"""Status indicator tests using fake SPI/LED hardware."""

import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from openscan_firmware.config.light import NeoPixelConfig
from openscan_firmware.controllers.hardware import neopixel as led
from openscan_firmware.controllers.hardware.motors import MotorController
from openscan_firmware.controllers.services.tasks.task_events import TaskEventPublisher
from openscan_firmware.models.scanner import ScannerDeviceConfig
from openscan_firmware.models.task import Task, TaskStatus


class NeoPixelTests(unittest.IsolatedAsyncioTestCase):
    """Exercise configuration, state selection and cleanup without a physical Pi."""

    def setUp(self):
        """Create fake hardware and an indicator using pixel 1 of a three-pixel chain."""
        self.pixels = MagicMock()
        self.spi = MagicMock()
        self.driver = MagicMock(return_value=self.pixels)
        self.modules = patch.dict(sys.modules, {
            "board": SimpleNamespace(D10=10, D11=11),
            "busio": SimpleNamespace(SPI=self.spi),
            "neopixel_spi": SimpleNamespace(NeoPixel_SPI=self.driver),
        })
        self.modules.start()
        led._scanning_tasks.clear()
        self.config = NeoPixelConfig(pixel_count=3, status_pixel=1)
        led.configure_status_led(self.config)
        self.scan = Task(name="Scan", task_type="scan_task", status=TaskStatus.RUNNING)
        self.hub = SimpleNamespace(broadcast_json=AsyncMock())
        self.publisher = TaskEventPublisher(hub_getter=lambda: self.hub)

    def tearDown(self):
        """Release the indicator and remove fake hardware and tracked test scans."""
        led.close_status_led()
        led._scanning_tasks.clear()
        self.modules.stop()

    def assert_color(self, color):
        """Check the last color assigned to the configured status pixel."""
        self.pixels.__setitem__.assert_called_with(1, color)

    def test_configuration_roundtrip_and_legacy_default(self):
        """Check saved settings survive loading and invalid strip settings are rejected."""
        legacy = ScannerDeviceConfig(name="Custom")
        self.assertIsNone(legacy.neopixel)
        legacy.neopixel = self.config
        restored = ScannerDeviceConfig.model_validate_json(legacy.model_dump_json())
        self.assertEqual(restored.neopixel, self.config)
        for invalid in (
            {"pixel_count": 0}, {"status_pixel": 1}, {"status_pixel": -1},
            {"brightness": 1.1}, {"ready_color": [256, 0, 0]},
            {"pixel_order": "RGBW"}, {"clock_pin": 10},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                NeoPixelConfig(**invalid)

    def test_initialization_and_disabled_configuration(self):
        """Check SPI setup and confirm disabling needs no hardware imports."""
        self.assert_color(self.config.ready_color)
        self.spi.assert_called_once_with(11, MOSI=10)
        self.driver.assert_called_once_with(
            self.spi.return_value, 3, pixel_order="GRB", brightness=0.1,
            auto_write=False, reset_time=0.0003,
        )
        led.configure_status_led(None)
        self.pixels.deinit.assert_called_once()
        self.spi.return_value.deinit.assert_called_once()
        with patch.dict(sys.modules, {"board": None, "busio": None, "neopixel_spi": None}):
            led.configure_status_led(None)
            led.update_scan_status(self.scan)
        self.assertEqual(self.driver.call_count, 1)

    async def test_scan_lifecycle_uses_task_events_without_repeated_writes(self):
        """Check scan events select colors while progress updates avoid extra writes."""
        await self.publisher.publish(self.scan)
        self.assert_color(self.config.scanning_color)
        writes = self.pixels.show.call_count
        await self.publisher.publish(self.scan)  # A progress update is not a color change.
        self.assertEqual(self.pixels.show.call_count, writes)
        for status in (TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.CANCELLED,
                       TaskStatus.ERROR, TaskStatus.INTERRUPTED, TaskStatus.PENDING):
            with self.subTest(status=status):
                self.scan.status = TaskStatus.RUNNING
                await self.publisher.publish(self.scan)
                self.scan.status = status
                await self.publisher.publish(self.scan)
                self.assert_color(self.config.ready_color)
        self.hub.broadcast_json.assert_awaited()

    def test_other_tasks_do_not_indicate_scanning(self):
        """Confirm unrelated background work leaves the indicator Ready."""
        led.update_scan_status(Task(name="Upload", task_type="cloud_upload_task", status=TaskStatus.RUNNING))
        self.assert_color(self.config.ready_color)

    def test_homing_priority_and_nested_operations(self):
        """Confirm nested homing overrides a scan until the outer operation exits."""
        led.update_scan_status(self.scan)
        with led.homing():
            self.assert_color(self.config.homing_color)
            with led.homing():
                self.assert_color(self.config.homing_color)
            self.assert_color(self.config.homing_color)
        self.assert_color(self.config.scanning_color)

    async def test_motor_home_keeps_homing_color_through_nested_calibration(self):
        """Check the real home/calibration methods keep Homing through every movement."""
        motor = object.__new__(MotorController)
        motor.settings = SimpleNamespace(home_angle=90)
        motor._calibrated = False

        async def movement(*args):
            """Stand in for motor movement and check its active indication."""
            self.assert_color(self.config.homing_color)

        async def home_event(event):
            """Simulate a home event that performs calibration before returning."""
            await motor.calibrate()
            self.assert_color(self.config.homing_color)

        motor.send_event = AsyncMock(side_effect=home_event)
        motor.move_to = AsyncMock(side_effect=movement)
        motor.move_to_endstop = AsyncMock(side_effect=movement)
        with patch("openscan_firmware.controllers.hardware.motors.asyncio.sleep", new=AsyncMock()):
            await motor.move_to_home()
        self.assertTrue(motor._calibrated)
        self.assert_color(self.config.ready_color)

    async def test_calibration_failure_and_cancellation_restore_scan_color(self):
        """Confirm failed or cancelled calibration restores the running scan indication."""
        led.update_scan_status(self.scan)
        motor = object.__new__(MotorController)
        motor._calibrated = False
        for error in (RuntimeError("movement failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error)):
                motor.move_to_endstop = AsyncMock(side_effect=error)
                with self.assertRaises(type(error)):
                    await motor.calibrate()
                self.assertFalse(motor._calibrated)
                self.assert_color(self.config.scanning_color)

    def test_reconfigure_releases_old_resources_and_uses_current_scan(self):
        """Check replacing the indicator releases SPI and retains the active scan state."""
        led.update_scan_status(self.scan)
        led.configure_status_led(self.config)
        self.pixels.deinit.assert_called_once()
        self.spi.return_value.deinit.assert_called_once()
        self.assert_color(self.config.scanning_color)

    def test_failed_initialization_releases_spi(self):
        """Confirm driver initialization failure releases an already opened SPI bus."""
        led.close_status_led()
        self.spi.return_value.reset_mock()
        self.driver.side_effect = RuntimeError("driver unavailable")
        with self.assertLogs(led.logger, level="ERROR"):
            led.configure_status_led(self.config)
        self.spi.return_value.deinit.assert_called_once()
        self.assertIsNone(led._pixels)

    def test_write_failure_does_not_interrupt_homing_or_cleanup(self):
        """Check a failed LED write neither escapes into homing nor prevents cleanup."""
        self.pixels.show.side_effect = RuntimeError("SPI unavailable")
        with self.assertLogs(led.logger, level="ERROR"):
            with led.homing():
                pass
        self.assertIsNone(led._pixels)
        self.assertEqual(led._homing_count, 0)
        self.spi.return_value.deinit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
