"""Behavior pins for the wire-scroll path across adapters.

The {direction, amount} wire action historically had two parse paths: the
base dispatcher (amount -> ~100px scroll units) and per-adapter _dispatch
overrides (exact pixel amount, center/element anchoring). These tests pin the
observable behavior of every adapter kind through the public ``execute()``
entry point, so consolidating the parsing into the base class cannot silently
change any adapter's gesture.
"""

from __future__ import annotations

from typing import Any

from qirabot.adapters.adb_adapter import AdbAdapter
from qirabot.adapters.appium_adapter import AppiumAdapter
from qirabot.adapters.wda_adapter import WdaAdapter


class FakeAppiumDriver:
    def __init__(self) -> None:
        self.capabilities = {"platformName": "Android"}

    def get_window_size(self) -> dict[str, int]:
        return {"width": 1000, "height": 2000}


def make_appium() -> tuple[AppiumAdapter, list[tuple[int, ...]]]:
    adapter = AppiumAdapter(FakeAppiumDriver())
    adapter._settle_override = 0.0
    drags: list[tuple[int, ...]] = []
    adapter.drag = lambda *a: drags.append(tuple(int(v) for v in a))  # type: ignore[method-assign]
    return adapter, drags


class TestAppiumWireScroll:
    """Appium had no scroll coverage at all; pin the swipe geometry it shares
    with the other touch adapters (same numbers as TestScrollGeometry in
    test_adb_adapter.py)."""

    def test_amount_is_exact_pixels_anchored_center(self):
        adapter, drags = make_appium()
        adapter.execute("scroll", {"direction": "down", "amount": 500})
        # center (500, 1000), scroll down = finger moves up by exactly 500px —
        # NOT the base dispatcher's amount/100 unit conversion.
        assert drags == [(500, 1000, 500, 500)]

    def test_scroll_at_anchors_on_element(self):
        adapter, drags = make_appium()
        adapter.execute(
            "scroll_at", {"direction": "up", "amount": 300, "x": 200, "y": 400}
        )
        assert drags == [(200, 400, 200, 700)]

    def test_zero_amount_defaults_to_60pct_span(self):
        adapter, drags = make_appium()
        adapter.execute("scroll", {"direction": "down"})
        # 0.6 * 2000 = 1200; 1000 - 1200 clamped to 0.05 * 2000 = 100
        assert drags == [(500, 1000, 500, 100)]

    def test_amount_capped_at_70pct_span(self):
        adapter, drags = make_appium()
        adapter.execute("scroll", {"direction": "up", "amount": 99999})
        # cap 0.7*2000=1400; 1000+1400=2400 -> clamp to 0.95*2000=1900
        assert drags == [(500, 1000, 500, 1900)]

    def test_horizontal_uses_width_span(self):
        adapter, drags = make_appium()
        adapter.execute("scroll", {"direction": "left", "amount": 400})
        assert drags == [(500, 1000, 900, 1000)]

    def test_legacy_scroll_units_via_direct_call(self):
        adapter, drags = make_appium()
        adapter.scroll(0, 0, "down", 3)  # legacy distance -> x100 px, center
        assert drags == [(500, 1000, 500, 700)]


class FakeAdbDevice:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    def shell(self, cmd: str) -> str:
        self.cmds.append(cmd)
        return ""


class TestAdbWireScrollViaExecute:
    """test_adb_adapter.py pins _scroll_action directly; this pins the full
    execute() route (dispatch override today, base dispatch after the
    consolidation)."""

    def make(self) -> tuple[AdbAdapter, FakeAdbDevice]:
        dev = FakeAdbDevice()
        adapter = AdbAdapter(dev)
        adapter._last_size = (1000, 2000)
        adapter._settle_override = 0.0
        return adapter, dev

    def swipes(self, dev: FakeAdbDevice) -> list[tuple[int, ...]]:
        out = []
        for cmd in dev.cmds:
            parts = cmd.split()
            assert parts[:2] == ["input", "swipe"]
            out.append(tuple(int(p) for p in parts[2:6]))
        return out

    def test_execute_scroll_honors_exact_pixel_amount(self):
        adapter, dev = self.make()
        adapter.execute("scroll", {"direction": "down", "amount": 500})
        assert self.swipes(dev) == [(500, 1000, 500, 500)]

    def test_execute_scroll_at_element_anchor(self):
        adapter, dev = self.make()
        adapter.execute(
            "scroll_at", {"direction": "up", "amount": 300, "x": 200, "y": 400}
        )
        assert self.swipes(dev) == [(200, 400, 200, 700)]


class FakeWdaClient:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def window_size(self) -> tuple[int, int]:
        return (393, 852)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0) -> None:
        self.calls.append(("swipe", x1, y1, x2, y2, duration))


class TestWdaWireScrollViaExecute:
    def make(self) -> tuple[WdaAdapter, FakeWdaClient]:
        client = FakeWdaClient()
        adapter = WdaAdapter(client)
        adapter._settle_override = 0.0
        return adapter, client

    def test_execute_scroll_honors_exact_pixel_amount(self):
        adapter, client = self.make()
        adapter.execute("scroll", {"direction": "down", "amount": 300})
        # center (196, 426) in points, finger moves up 300, instant swipe
        assert client.calls == [("swipe", 196, 426, 196, 126, 0)]

    def test_execute_scroll_at_element_anchor(self):
        adapter, client = self.make()
        adapter.execute(
            "scroll_at", {"direction": "down", "amount": 100, "x": 100, "y": 500}
        )
        assert client.calls == [("swipe", 100, 500, 100, 400, 0)]
