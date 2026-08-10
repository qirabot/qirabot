#!/usr/bin/env python3
"""Verify that an NSWindowSharingNone overlay is excluded from the screenshot
path qirabot's desktop backend uses on macOS (pyautogui -> `screencapture`).

Run this ON THE MAC, inside a normal GUI login session:

    uv pip install pyobjc-framework-Cocoa pillow   # pyautogui optional but preferred
    python scripts/verify_overlay_mac.py

The terminal app needs Screen Recording permission (System Settings ->
Privacy & Security -> Screen Recording). Don't worry about getting that
wrong: phase B below detects a missing permission and says so.

Two phases:
  A. show a solid-magenta panel with sharingType=None  -> screenshot must NOT contain it
  B. flip the same panel back to capturable            -> screenshot MUST contain it
B is the control that proves the detection method works at all.

Screenshots of the scanned region are saved to /tmp/qirabot_overlay_{a,b}.png
so you can eyeball them.
"""

import subprocess
import sys
import tempfile

if sys.platform != "darwin":
    sys.exit("This script must run on macOS.")

try:
    import AppKit
    from AppKit import NSColor, NSDate, NSPanel, NSRunLoop
except ImportError:
    sys.exit("pyobjc is required: uv pip install pyobjc-framework-Cocoa")

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: uv pip install pillow")

PANEL_W, PANEL_H, MARGIN = 260, 100, 40
# After thumbnailing the scanned quadrant to <=600px wide, the panel covers
# hundreds of pixels; 20 magenta-ish pixels is a safe "it's there" threshold.
MAGENTA_THRESHOLD = 20


def pump(seconds: float) -> None:
    """Let the Cocoa run loop breathe so the panel actually paints."""
    NSRunLoop.currentRunLoop().runUntilDate_(
        NSDate.dateWithTimeIntervalSinceNow_(seconds)
    )


def screenshot():
    """Full-screen capture via the same path qirabot uses, PIL Image out."""
    try:
        import pyautogui

        return pyautogui.screenshot(), "pyautogui.screenshot()"
    except Exception:
        path = tempfile.mktemp(suffix=".png")
        subprocess.run(["screencapture", "-x", "-m", path], check=True)
        return Image.open(path), "screencapture -x -m"


def magenta_count(img: Image.Image, save_as: str) -> int:
    """Count magenta-ish pixels in the bottom-right quadrant."""
    w, h = img.size
    quad = img.crop((w // 2, h // 2, w, h)).convert("RGB")
    quad.thumbnail((600, 600))
    quad.save(save_as)
    return sum(
        1 for r, g, b in quad.getdata() if r > 180 and b > 180 and g < 90
    )


def main() -> None:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    screen = AppKit.NSScreen.mainScreen().frame()
    # NSWindow coordinates: origin is bottom-left, so this is the
    # bottom-right corner of the main display.
    rect = AppKit.NSMakeRect(
        screen.size.width - PANEL_W - MARGIN, MARGIN, PANEL_W, PANEL_H
    )
    style = (
        AppKit.NSWindowStyleMaskBorderless
        | AppKit.NSWindowStyleMaskNonactivatingPanel
    )
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, AppKit.NSBackingStoreBuffered, False
    )
    panel.setBackgroundColor_(NSColor.magentaColor())
    panel.setOpaque_(True)
    panel.setLevel_(AppKit.NSStatusWindowLevel)
    panel.setIgnoresMouseEvents_(True)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
    )

    # --- Phase A: excluded from capture -----------------------------------
    panel.setSharingType_(AppKit.NSWindowSharingNone)
    panel.orderFrontRegardless()
    pump(1.5)
    img, source = screenshot()
    a = magenta_count(img, "/tmp/qirabot_overlay_a.png")

    # --- Phase B: control, same panel made capturable ---------------------
    panel.setSharingType_(AppKit.NSWindowSharingReadOnly)
    panel.orderFrontRegardless()
    pump(1.0)
    img, _ = screenshot()
    b = magenta_count(img, "/tmp/qirabot_overlay_b.png")

    panel.orderOut_(None)

    print(f"screenshot path : {source}")
    print(f"phase A (sharingType=None)     magenta pixels: {a}")
    print(f"phase B (sharingType=ReadOnly) magenta pixels: {b}")
    print("saved: /tmp/qirabot_overlay_a.png /tmp/qirabot_overlay_b.png")

    if b < MAGENTA_THRESHOLD:
        print(
            "\nINCONCLUSIVE: the control panel is missing from the screenshot"
            " too.\nMost likely the terminal lacks Screen Recording permission"
            " (System Settings -> Privacy & Security -> Screen Recording),"
            " or the panel is on another display (script uses the main one)."
        )
        sys.exit(2)
    if a < MAGENTA_THRESHOLD:
        print("\nPASS: sharingType=None window is excluded from capture.")
        sys.exit(0)
    print(
        "\nFAIL: the overlay shows up in screenshots despite sharingType=None"
        " -- this macOS version does not honor it on this capture path."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
