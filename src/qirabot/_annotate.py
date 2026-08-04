"""Step-image rendering for the run report: annotation + thumbnail.

Pure image work, no client state: one PIL decode produces both the full-res
annotated screenshot and the embedded thumbnail. Kept out of client.py so the
SDK facade doesn't carry drawing code.
"""

from __future__ import annotations

import io

from qirabot.adapters.base import ScreenshotConfig


def render_step_images(
    data: bytes,
    coords: tuple[float, float] | None,
    config: ScreenshotConfig | None = None,
    *,
    end_coords: tuple[float, float] | None = None,
    coord_scale: float = 1.0,
    thumb_max_edge: int = 800,
    thumb_quality: int = 60,
) -> tuple[bytes, str]:
    """Decode the screenshot once → (full-res encoded bytes, thumbnail data URI).

    Annotates a crosshair at ``coords`` when given and ``config.annotate`` is on;
    otherwise the full-res output is just the source re-encoded in the configured
    format. ``end_coords`` is drag's terminal point — when set, a line + arrow is
    drawn from ``coords`` to it and a hollow ring marks the end. ``coord_scale``
    maps the model's coordinate space onto the screenshot pixel space — 1.0
    everywhere except Appium iOS, where coords arrive in logical points and the
    screenshot is at physical Retina pixels. The thumbnail is always a downscaled
    JPEG embedded as a data URI so the HTML report stays self-contained.
    """
    import base64
    import math

    from PIL import Image, ImageDraw

    cfg = config or ScreenshotConfig()
    img: Image.Image = Image.open(io.BytesIO(data))

    if coords is not None and cfg.annotate:
        img = img.convert("RGBA")
        draw = ImageDraw.Draw(img)
        color = (255, 0, 0, 255)
        short_side = min(img.width, img.height)
        radius = max(4, round(short_side * 0.015))
        line_len = int(radius * 1.5)
        width = 3 if short_side > 2000 else 2
        gap = radius + 2
        cx, cy = int(coords[0] * coord_scale), int(coords[1] * coord_scale)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=color, width=width,
        )
        draw.line([(cx - gap - line_len, cy), (cx - gap, cy)], fill=color, width=width)
        draw.line([(cx + gap, cy), (cx + gap + line_len, cy)], fill=color, width=width)
        draw.line([(cx, cy - gap - line_len), (cx, cy - gap)], fill=color, width=width)
        draw.line([(cx, cy + gap), (cx, cy + gap + line_len)], fill=color, width=width)

        if end_coords is not None:
            ex, ey = int(end_coords[0] * coord_scale), int(end_coords[1] * coord_scale)
            dx, dy = ex - cx, ey - cy
            dist = math.hypot(dx, dy)
            # Skip degenerate drags (start == end) — a zero-length arrow would
            # just draw an artifact on top of the start cross.
            if dist >= 1:
                ux, uy = dx / dist, dy / dist
                # Stop the shaft outside the end ring so they don't overlap.
                sx = cx + int(ux * (radius + gap))
                sy = cy + int(uy * (radius + gap))
                tx = ex - int(ux * (radius + gap))
                ty = ey - int(uy * (radius + gap))
                draw.line([(sx, sy), (tx, ty)], fill=color, width=width)
                # Arrowhead: two short segments rotated ±25° back from the tip.
                head_len = max(8, radius * 2)
                angle = math.atan2(uy, ux)
                for offset in (math.radians(150), math.radians(-150)):
                    hx = tx + int(math.cos(angle + offset) * head_len)
                    hy = ty + int(math.sin(angle + offset) * head_len)
                    draw.line([(tx, ty), (hx, hy)], fill=color, width=width)
                # Hollow end ring, same radius as the start cross's circle so the
                # two endpoints read as a matched pair.
                draw.ellipse(
                    [ex - radius, ey - radius, ex + radius, ey + radius],
                    outline=color, width=width,
                )

    # Full-res output in the configured format (jpeg has no alpha channel, so
    # flatten RGBA → RGB first).
    full_buf = io.BytesIO()
    if cfg.format == "jpeg":
        img.convert("RGB").save(full_buf, format="JPEG", quality=cfg.quality)
    else:
        img.save(full_buf, format="PNG")
    full_bytes = full_buf.getvalue()

    # Thumbnail derived from the same in-memory image — no second decode.
    thumb_img = img if img.mode == "RGB" else img.convert("RGB")
    longest = max(thumb_img.width, thumb_img.height)
    if longest > thumb_max_edge:
        scale = thumb_max_edge / longest
        thumb_img = thumb_img.resize(
            (max(1, round(thumb_img.width * scale)), max(1, round(thumb_img.height * scale)))
        )
    thumb_buf = io.BytesIO()
    thumb_img.save(thumb_buf, format="JPEG", quality=thumb_quality)
    thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(thumb_buf.getvalue()).decode("ascii")

    return full_bytes, thumb_b64
