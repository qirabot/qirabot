"""rescale_point / bbox_center — the engine's coordinate contract,
including the raw-pixel salvage semantics."""

from qirabot.engine.coords import bbox_center, rescale_point, to_float


class TestRescalePoint:
    def test_normalized(self) -> None:
        assert rescale_point(500, 500, 1280, 720) == (640, 360, "normalized")

    def test_normalized_bounds(self) -> None:
        assert rescale_point(0, 0, 1280, 720) == (0, 0, "normalized")
        assert rescale_point(1000, 1000, 1280, 720) == (1280, 720, "normalized")

    def test_normalized_rounding_half_away(self) -> None:
        # 1/1000*500 = 0.5 must round half-away-from-zero to 1, not
        # bankers' 0.
        assert rescale_point(1, 1, 500, 500) == (1, 1, "normalized")

    def test_pixel_salvage_both_axes_in_frame(self) -> None:
        # BOTH axes >1000 and both fit the frame: unambiguous raw-pixel
        # emission (qwen-VL occasionally does this), taken as-is.
        assert rescale_point(1100, 1050, 1280, 1100) == (1100, 1050, "pixel")

    def test_single_axis_over_1000_is_unusable(self) -> None:
        # Ambiguous: a pixel value, or a normalized overshoot whose partner
        # axis is still normalized. Caller must re-decide, not guess.
        assert rescale_point(1500, 400, 1280, 720) == (0, 0, "")
        assert rescale_point(400, 1500, 1280, 1600) == (0, 0, "")

    def test_pixel_out_of_frame_is_unusable(self) -> None:
        assert rescale_point(2000, 2000, 1280, 720) == (0, 0, "")

    def test_negative_is_unusable(self) -> None:
        assert rescale_point(-1, 500, 1280, 720) == (0, 0, "")
        assert rescale_point(500, -1, 1280, 720) == (0, 0, "")

    def test_non_numeric_is_unusable(self) -> None:
        assert rescale_point("500", 500, 1280, 720) == (0, 0, "")
        assert rescale_point(None, None, 1280, 720) == (0, 0, "")

    def test_bool_is_not_a_coordinate(self) -> None:
        # bool is an int subclass in Python; it must not pass as a number.
        assert rescale_point(True, 500, 1280, 720) == (0, 0, "")

    def test_zero_frame_is_unusable(self) -> None:
        assert rescale_point(500, 500, 0, 720) == (0, 0, "")
        assert rescale_point(500, 500, 1280, 0) == (0, 0, "")

    def test_float_inputs(self) -> None:
        assert rescale_point(499.6, 500.4, 1000, 1000) == (500, 500, "normalized")


class TestBboxCenter:
    def test_center(self) -> None:
        # box_2d is [ymin, xmin, ymax, xmax] normalized to 0-1000.
        assert bbox_center([100, 200, 300, 400], 1000, 1000) == (300, 200)

    def test_scales_to_frame(self) -> None:
        assert bbox_center([0, 0, 1000, 1000], 1280, 720) == (640, 360)


class TestToFloat:
    def test_numbers(self) -> None:
        assert to_float(3) == 3.0
        assert to_float(3.5) == 3.5

    def test_rejects_bool_str_none(self) -> None:
        assert to_float(True) is None
        assert to_float("3") is None
        assert to_float(None) is None
