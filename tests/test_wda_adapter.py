"""Tests for the direct-WDA iOS adapter (fake WdaClient, no HTTP)."""

from __future__ import annotations

import io

import pytest

from qirabot.adapters.base import ScreenshotConfig
from qirabot.adapters.wda_adapter import WdaAdapter
from qirabot.wda import WdaClient


def tiny_png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(buf, format="PNG")
    return buf.getvalue()


class FakeClient(WdaClient):
    """WdaClient with the HTTP layer replaced by call recording."""

    def __init__(self, *, size=(393, 852), png=None):
        # deliberately no super().__init__: no httpx client needed
        self.calls: list[tuple] = []
        self._fake_size = size
        self._png = png or tiny_png(786, 1704)  # 2x Retina of 393x852

    def screenshot(self) -> bytes:
        self.calls.append(("screenshot",))
        return self._png

    def window_size(self):
        self.calls.append(("window_size",))
        return self._fake_size

    def rotate(self, size, png):
        """Device rotation: WDA now reports the other orientation's point
        size and hands back frames in that orientation."""
        self._fake_size = size
        self._png = png

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def double_tap(self, x, y):
        self.calls.append(("double_tap", x, y))

    def tap_hold(self, x, y, duration=1.0):
        self.calls.append(("tap_hold", x, y, duration))

    def swipe(self, x1, y1, x2, y2, duration=0):
        self.calls.append(("swipe", x1, y1, x2, y2, duration))

    def send_keys(self, text):
        self.calls.append(("send_keys", text))

    def press_button(self, name):
        self.calls.append(("press_button", name))

    def home(self):
        self.calls.append(("home",))

    def lock(self):
        self.calls.append(("lock",))


class TestDetectionAndInfo:
    def test_accepts_only_wda_client(self):
        assert WdaAdapter.accepts(FakeClient())
        assert not WdaAdapter.accepts(object())

    def test_auto_detect_routes_to_wda_adapter(self):
        from qirabot.adapters.auto import detect

        assert isinstance(detect(FakeClient()), WdaAdapter)

    def test_device_info_reports_logical_points(self):
        info = WdaAdapter(FakeClient()).device_info()
        assert (info.platform, info.width, info.height) == ("ios", 393, 852)

    def test_annotation_scale_from_retina_png(self):
        adapter = WdaAdapter(FakeClient())
        adapter.screenshot(ScreenshotConfig(format="png"))
        assert adapter.annotation_scale() == pytest.approx(2.0)

    def test_annotation_scale_defaults_to_one(self):
        assert WdaAdapter(FakeClient()).annotation_scale() == 1.0

    def test_screenshot_png_passthrough_and_jpeg(self):
        png = tiny_png(4, 4)
        adapter = WdaAdapter(FakeClient(size=(4, 4), png=png))
        assert adapter.screenshot(ScreenshotConfig(format="png")) == png
        assert adapter.screenshot(ScreenshotConfig(format="jpeg"))[:3] == b"\xff\xd8\xff"


class TestRotation:
    """A rotated device reports a different point size; a cache that outlived
    the rotation would scale every model coordinate against the old screen."""

    def portrait(self):
        client = FakeClient()  # 393x852 points, 786x1704 frame
        adapter = WdaAdapter(client)
        adapter.screenshot(ScreenshotConfig(format="png"))
        return adapter, client

    def test_rotation_refreshes_logical_size(self):
        adapter, client = self.portrait()
        assert (adapter.device_info().width, adapter.device_info().height) == (393, 852)

        client.rotate((852, 393), tiny_png(1704, 786))
        adapter.screenshot(ScreenshotConfig(format="png"))

        info = adapter.device_info()
        assert (info.width, info.height) == (852, 393)
        assert adapter.annotation_scale() == pytest.approx(2.0)

    def test_rotation_moves_geometry_derived_gestures(self):
        adapter, client = self.portrait()
        client.rotate((852, 393), tiny_png(1704, 786))
        adapter.screenshot(ScreenshotConfig(format="png"))

        adapter.go_back()
        # left edge across 60% of the landscape 852pt width, at mid-height
        assert client.calls[-1] == ("swipe", 1, 196, 511, 196, 0)

    def test_same_orientation_keeps_size_cached(self):
        adapter, client = self.portrait()
        for _ in range(3):
            adapter.screenshot(ScreenshotConfig(format="png"))
            adapter.device_info()
        # one probe at the first frame, none after: no per-step HTTP round-trip
        assert client.calls.count(("window_size",)) == 1


class TestActions:
    def make(self):
        client = FakeClient()
        return WdaAdapter(client), client

    def test_pointer_mapping(self):
        adapter, client = self.make()
        adapter.click(10.7, 20.2)
        adapter.double_click(1, 2)
        adapter.long_press(3, 4, duration=2.5)
        adapter.drag(0, 0, 50, 60)
        assert client.calls == [
            ("tap", 10, 20),
            ("double_tap", 1, 2),
            ("tap_hold", 3, 4, 2.5),
            ("swipe", 0, 0, 50, 60, 0.5),
        ]

    def test_type_text_taps_then_sends(self, monkeypatch):
        adapter, client = self.make()
        monkeypatch.setattr("time.sleep", lambda s: None)
        adapter.type_text(5, 6, "你好")
        assert client.calls == [("tap", 5, 6), ("send_keys", "你好")]

    def test_clear_focused_sends_backspaces(self):
        adapter, client = self.make()
        adapter.clear_focused()
        assert client.calls == [("send_keys", "\b" * 64)]

    def test_press_key_mapping(self):
        adapter, client = self.make()
        adapter.press_key("Enter")
        adapter.press_key("Backspace")
        adapter.press_key("home")
        adapter.press_key("volume_up")
        adapter.press_key("lock")
        assert client.calls == [
            ("send_keys", "\n"),
            ("send_keys", "\b"),
            ("home",),
            ("press_button", "volumeUp"),
            ("lock",),
        ]

    def test_unknown_key_raises(self):
        adapter, _ = self.make()
        with pytest.raises(NotImplementedError):
            adapter.press_key("F5")

    def test_go_back_is_left_edge_swipe(self):
        adapter, client = self.make()
        adapter.go_back()
        # from the left edge, horizontally across 60% of the 393pt width
        assert client.calls[-1] == ("swipe", 1, 426, 235, 426, 0)

    def test_scroll_amount_center_anchor(self):
        adapter, client = self.make()
        adapter._scroll_action("scroll", {"direction": "down", "amount": 300})
        # center (196, 426) in points, finger moves up 300
        assert client.calls[-1] == ("swipe", 196, 426, 196, 126, 0)

    def test_scroll_capped_at_70pct_span(self):
        adapter, client = self.make()
        adapter._scroll_action("scroll", {"direction": "up", "amount": 5000})
        # cap 0.7*852=596; 426+596=1022 -> clamped to 0.95*852=809
        assert client.calls[-1] == ("swipe", 196, 426, 196, 809, 0)
