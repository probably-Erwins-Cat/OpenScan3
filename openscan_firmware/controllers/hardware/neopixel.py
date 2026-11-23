"""Optional three-state NeoPixel indicator. Hardware imports are lazy."""

import logging
from contextlib import contextmanager

from openscan_firmware.config.light import NeoPixelConfig
from openscan_firmware.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)

_pixels = None
_spi = None
_config: NeoPixelConfig | None = None
_state: str | None = None
_homing_count = 0
_scanning_tasks: set[str] = set()


def close_status_led() -> None:
    """Blank the chain and release SPI, including after partial initialization.

    Keep operation tracking so a replacement indicator shows the current state.
    Cleanup failures are logged and do not prevent other resources being closed.
    """
    global _pixels, _spi, _config, _state
    pixels, spi = _pixels, _spi
    _pixels = _spi = _config = _state = None
    for resource in (pixels, spi):
        if resource is not None:
            try:
                resource.deinit()
            except Exception:
                logger.exception("Could not close NeoPixel status indicator resource")


def configure_status_led(config: NeoPixelConfig | None) -> None:
    """Replace the indicator and immediately display the current operation state.

    None disables the indicator without importing hardware libraries. Driver or
    SPI failures are logged and leave the optional indicator disabled.
    """
    global _pixels, _spi, _config
    close_status_led()
    if config is None:
        return
    try:
        import board
        import busio
        from neopixel_spi import NeoPixel_SPI

        # Do not reserve MISO: other scanner hardware may use that GPIO.
        _spi = busio.SPI(
            getattr(board, f"D{config.clock_pin}"),
            MOSI=getattr(board, f"D{config.data_pin}"),
        )
        _pixels = NeoPixel_SPI(
            _spi, config.pixel_count, pixel_order=config.pixel_order,
            brightness=config.brightness, auto_write=False, reset_time=0.0003,
        )
        _config = config
        _refresh()
    except Exception:
        logger.exception("NeoPixel status indicator unavailable; check SPI and the neopixel extra")
        close_status_led()


def _refresh() -> None:
    """Display Homing before Scanning before Ready, writing only on changes.

    Disable the indicator if a write fails so scanner operations can continue.
    """
    global _state
    if _pixels is None:
        return
    state = "homing" if _homing_count else "scanning" if _scanning_tasks else "ready"
    if state == _state:
        return
    try:
        _pixels[_config.status_pixel] = getattr(_config, f"{state}_color")
        _pixels.show()
        _state = state
    except Exception:
        # An optional indicator must never interrupt a scan or motor cleanup.
        logger.exception("NeoPixel status update failed; disabling indicator")
        close_status_led()


def update_scan_status(task: Task) -> None:
    """Track running scan IDs from task events and refresh the indication.

    Ignore other task types; paused, pending and finished scans are not running.
    """
    if task.task_type != "scan_task":
        return
    if task.status == TaskStatus.RUNNING:
        _scanning_tasks.add(task.id)
    else:
        _scanning_tasks.discard(task.id)
    _refresh()


@contextmanager
def homing():
    """Show Homing while the enclosed motor operation runs.

    Count nested/concurrent operations so Homing remains until the last exits.
    Restore Scanning or Ready on exit, including exceptions and cancellation.
    """
    global _homing_count
    _homing_count += 1
    _refresh()
    try:
        yield
    finally:
        _homing_count -= 1
        _refresh()
