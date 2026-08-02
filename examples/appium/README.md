# Appium + Qirabot

Use Qirabot as a bolt-on AI layer on top of your existing Appium mobile tests.

AI is especially useful on mobile — element IDs change across Android versions, OEMs, and iOS updates.

## Install

```bash
python -m pip install qirabot Appium-Python-Client pytest
gcloud auth application-default login   # auth: Google Cloud ADC, once
```

## Start Appium server

**Android:**

```bash
npx appium driver install uiautomator2
npx appium
```

**iOS:**

```bash
npx appium driver install xcuitest
npx appium
```

## Start device

- Android: start an emulator or connect a device via USB
- iOS: start a simulator. The iOS example selects the device by `deviceName`
  only, which works for simulators; a real iOS device additionally needs the
  `appium:udid` capability (from `xcrun xctrace list devices`) plus a signed
  WebDriverAgent — add `options.udid = "..."` if you adapt the example for one.

## Run

```bash
# Android
pytest examples/appium/test_android_settings.py

# iOS
pytest examples/appium/test_ios_settings.py
```

Environment variables:

```bash
export APPIUM_URL=http://localhost:4723      # default
export ANDROID_DEVICE=emulator-5554          # default
export IOS_DEVICE="iPhone 16"                # default (simulator device type name)
```

## How it works

```python
import pytest
from appium import webdriver
from appium.options.android import UiAutomator2Options
from qirabot import Qirabot

@pytest.fixture(scope="session")
def driver():
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.device_name = "emulator-5554"
    options.app_package = "com.example.myapp"
    options.app_activity = ".MainActivity"
    driver = webdriver.Remote("http://localhost:4723", options=options)
    yield driver
    driver.quit()

@pytest.fixture(scope="session")
def bot(driver):
    # bind once; the driver is stable across the session. Closed after the last test.
    with Qirabot(task_name="my-test").bind(driver) as bot:
        yield bot

def test_login(driver, bot):
    # Your existing Appium code (native driver calls unchanged)
    driver.find_element("id", "com.example.myapp:id/login_btn").click()

    # Bolt-on: AI verifies — works on any Android version
    assert bot.verify("Home screen is displayed")

    # Bolt-on: AI navigates when IDs are unreliable
    result = bot.ai("Go to Settings and enable notifications", max_steps=8)
    assert result.success
```

## Examples

Bolt-on to your existing tests (run with `pytest`):

- [test_android_settings.py](test_android_settings.py) — Android Settings: display, search, dark mode
- [test_ios_settings.py](test_ios_settings.py) — iOS Settings: Wi-Fi, airplane mode, device info
- [test_android_app.py](test_android_app.py) — Template: replace with your own app

Standalone RPA — drive the phone to finish a task (run with `python`):

- [mobile_rpa.py](mobile_rpa.py) — hand a whole task to `bot.ai()`, no pytest
