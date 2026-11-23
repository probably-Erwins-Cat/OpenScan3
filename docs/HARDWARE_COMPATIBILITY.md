# Hardware Compatibility

## Reference setup

The primary development combinations are:

| Raspberry Pi | Camera |
| --- | --- |
| Raspberry Pi 4, 2 GB RAM | Arducam IMX519 |
| Raspberry Pi 4, 2 GB RAM | Arducam Hawkeye |

Before a new OpenScan3 release, real tests are made with this setup.

## Other Raspberry Pi Models

**Raspberry Pi 3**. Reportedly works with Arducam IMX519.

**Raspberry Pi 5**. Reportedly works with Arducam IMX519, use CAM0 slot.


## Optional NeoPixel status indicator

Any shield can use an RGB NeoPixel chain as a status indicator. Install the
optional driver in the firmware's Python environment (`pip install '.[neopixel]'`
from a source checkout), enable the chosen hardware SPI bus in Raspberry Pi OS,
and give the firmware service user access to its `/dev/spidev*` device.

Add this optional top-level section to your device configuration, then reload
that configuration or restart the firmware:

```json
"neopixel": {
    "data_pin": 10,
    "clock_pin": 11,
    "pixel_count": 1,
    "status_pixel": 0,
    "pixel_order": "GRB",
    "brightness": 0.1,
    "ready_color": [0, 255, 0],
    "scanning_color": [0, 0, 255],
    "homing_color": [255, 255, 0]
}
```

Pins use BCM numbering and must form a hardware SPI MOSI/clock pair supported
by Adafruit Blinka. Reserve that SPI bus for the LEDs; do not share its pins
with motors or other peripherals. Only MOSI connects to LED data; the clock
pin is reserved for SPI but does not connect to the LEDs. Supply suitable LED
power, a common ground, and data level shifting where required.

Homing/calibration takes priority over scanning. Only a running `scan_task`
indicates scanning. Everything else, including paused scans and device sleep,
shows Ready. Colors change only on state transitions; there are no animations.
Ready means no scan or homing operation is active, not a hardware health check.

On Black Shield V2.8, the onboard LED is pixel 0 on GPIO10. If external LEDs
are attached to J26, include them in `pixel_count`; they start at index 1.
Only `status_pixel` is colored, and other pixels are sent as off. Shutdown or
configuration reload blanks the chain and releases SPI. A 300 microsecond
reset interval is used for the onboard WS2812B-2020-V6.

Omit `neopixel` or set it to `null` to disable the feature (the default).
The driver is imported only when configured; SPI/driver failures are logged
and disable the indicator without stopping the scanner.

## Other Cameras

| Camera                             | Status | Notes                                                                                                                                                       |
|------------------------------------| --- |-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Manual camera trigger via GPIO     | **Out of the box** | Trigger arbitrary cameras offering shutter control. See [Camera via External Trigger](Camera/EXTERNAL_TRIGGER.md).                                          |
| GPhoto2 cameras                    | **Fine tuning required** | Camera-specific profiles and settings may need to be added or adjusted. See [Add a GPhoto2 Camera Profile](Camera/GPHOTO2_ADD_CAMERA.md).                   |
| LinuxPy/V4L2 cameras (e.g. Webcam) | **Fine tuning required** | Generic capture is available, but camera controls, autofocus, cropping, and RAW support vary by device. See [LinuxPy Camera Controller](Camera/LINUXPY.md). |
| Raspberry Pi Camera Module 3       | **Experimental** | Starts and takes pictures. Requires validation of the Picamera2/libcamera configuration and camera behavior.                                                |


Status definitions:

- **Out of the box**: included in the supported OpenScan3 hardware path and
  usable without firmware changes.
- **Fine tuning required**: the generic integration exists, but camera-specific
  profiles, controls, or configuration are likely needed.
- **Experimental**: an integration or platform path exists, and some features work, but the complete
  setup still needs testing on the target hardware.

