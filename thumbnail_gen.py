"""
thumbnail_gen.py — YouTube Thumbnail Generator for The Bias Files

Generates branded 1280×720 thumbnails using PIL/Pillow.
Saves as high-quality JPG.

Brand colors:
    Background  : #1A1A1A (charcoal)
    Loss/danger  : #E63946 (red)
    Gain/value   : #D4AF37 (gold)
    Watermark    : #D4AF37 at 70 % opacity
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

# ── Brand palette ────────────────────────────────────────────────
BG_CHARCOAL = "#1A1A1A"
COLOR_RED = "#E63946"
COLOR_GOLD = "#D4AF37"
COLOR_WHITE = "#FFFFFF"

# Gold at 70 % opacity → RGBA tuple
_WATERMARK_RGBA = (212, 175, 55, int(255 * 0.70))

# ── Font paths (Windows) ────────────────────────────────────────
_FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"
_FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
_FONT_BLACK = "C:/Windows/Fonts/ariblk.ttf"  # Arial Black


# ── Helpers ──────────────────────────────────────────────────────

def _get_font(
    size: int,
    bold: bool = False,
    black: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load an Arial TrueType font, falling back gracefully.

    Priority when *black* is True:  Arial Black → Arial Bold → Arial → default.
    Priority when *bold*  is True:  Arial Bold → Arial → default.
    Otherwise:                      Arial → default.
    """
    if black:
        candidates = [_FONT_BLACK, _FONT_BOLD, _FONT_REGULAR]
    elif bold:
        candidates = [_FONT_BOLD, _FONT_REGULAR]
    else:
        candidates = [_FONT_REGULAR]

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue

    return ImageFont.load_default()


def _wrap_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Word-wrap *text* so each line fits within *max_width* pixels."""
    if not hasattr(font, "getlength"):
        avg_char = max(max_width // 14, 1)
        return textwrap.wrap(text, width=avg_char)

    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        if font.getlength(test) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or [""]


def _get_line_height(font: ImageFont.FreeTypeFont) -> int:
    """Get line height, compatible with Pillow <9.2 (no getbbox) and newer."""
    if hasattr(font, "getbbox"):
        bbox = font.getbbox("Ay")
        return bbox[3] - bbox[1]
    ascent, descent = font.getmetrics()
    return ascent + descent


def _text_block_height(
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    line_spacing: int = 8,
) -> int:
    line_h = _get_line_height(font)
    return len(lines) * line_h + max(len(lines) - 1, 0) * line_spacing


def _draw_centered_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    color: str,
    center_x: int,
    y_start: int,
    max_width: int,
    line_spacing: int = 8,
) -> int:
    """Draw centred, wrapped text and return the Y coordinate after the block."""
    lines = _wrap_text(text, font, max_width)
    line_h = _get_line_height(font)

    y = y_start
    for line in lines:
        lw = font.getlength(line)
        x = center_x - lw / 2
        draw.text((x, y), line, fill=color, font=font)
        y += line_h + line_spacing
    return y


def _add_watermark(img: Image.Image, text: str = "THE BIAS FILES") -> None:
    """Stamp a small gold watermark at the bottom-right corner.

    Uses an RGBA overlay so the watermark is semi-transparent even on
    the RGB base image.
    """
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _get_font(28, bold=True)

    tw = font.getlength(text)
    margin = 30
    x = img.width - tw - margin
    y = img.height - 50

    draw.text((x, y), text, fill=_WATERMARK_RGBA, font=font)

    # Composite onto the base (convert base to RGBA then back to RGB)
    base_rgba = img.convert("RGBA")
    composite = Image.alpha_composite(base_rgba, overlay)
    # Copy pixels back into the original image in-place
    img.paste(composite.convert("RGB"))


# ── Public API ───────────────────────────────────────────────────

def generate_thumbnail(
    title_text: str,
    output_path: str | Path,
    *,
    variant: Literal["A", "B"] = "A",
    width: int = 1280,
    height: int = 720,
    loss_text: str | None = None,
    gain_text: str | None = None,
    background_image: str | Path | None = None,
) -> Path:
    """Generate a branded YouTube thumbnail.

    **Variant A** (default):
        Charcoal background. Loss amount in red on the left half,
        gain amount in gold on the right half.  Full title centred above.
        Gold watermark bottom-right at 70 % opacity.

    **Variant B**:
        Same layout with a diagonal white divider line.

    If *loss_text* / *gain_text* are ``None`` the function attempts to
    split *title_text* at ``" vs "`` (case-insensitive).  If that fails
    the entire *title_text* is rendered centred in white.

    If *background_image* is provided, it is used as the canvas background
    instead of solid charcoal (resized to fill, maintaining aspect ratio).

    Parameters
    ----------
    title_text : str
        Main thumbnail title.
    output_path : str | Path
        Destination file (saved as high-quality JPG).
    variant : ``'A'`` | ``'B'``
        Layout variant.
    width, height : int
        Thumbnail dimensions (default 1280×720).
    loss_text, gain_text : str, optional
        Explicit left (red) and right (gold) labels.
    background_image : str | Path, optional
        Path to an image to use as background instead of solid charcoal.

    Returns
    -------
    Path
        Absolute path to the saved JPG.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if background_image:
        bg = Image.open(background_image).convert("RGB")
        bg = bg.resize((width, height), Image.LANCZOS)
        img = Image.new("RGB", (width, height))
        img.paste(bg, (0, 0))
    else:
        img = Image.new("RGB", (width, height), BG_CHARCOAL)
    draw = ImageDraw.Draw(img)

    font_title = _get_font(52, black=True)
    font_big = _get_font(72, black=True)

    half_w = width // 2
    margin = 40
    max_side = half_w - margin * 2
    max_title = int(width * 0.85)

    # ── Auto-split if needed ─────────────────────────────────────
    if loss_text is None or gain_text is None:
        lower = title_text.lower()
        if " vs " in lower:
            idx = lower.index(" vs ")
            loss_text = title_text[:idx].strip()
            gain_text = title_text[idx + 4:].strip()
        else:
            loss_text = None
            gain_text = None

    if loss_text and gain_text:
        # ── Title across the top ─────────────────────────────────
        title_y = int(height * 0.08)
        _draw_centered_block(
            draw, title_text, font_title, COLOR_WHITE,
            center_x=half_w, y_start=title_y, max_width=max_title,
        )

        # ── Left side — loss (red) ──────────────────────────────
        left_lines = _wrap_text(loss_text, font_big, max_side)
        left_h = _text_block_height(left_lines, font_big)
        y_left = int((height - left_h) / 2) + 40  # shift slightly below title

        bbox_big = font_big.getbbox("Ay")
        line_h = bbox_big[3] - bbox_big[1]

        for line in left_lines:
            lw = font_big.getlength(line)
            x = (half_w - lw) / 2
            draw.text((x, y_left), line, fill=COLOR_RED, font=font_big)
            y_left += line_h + 8

        # ── Right side — gain (gold) ────────────────────────────
        right_lines = _wrap_text(gain_text, font_big, max_side)
        right_h = _text_block_height(right_lines, font_big)
        y_right = int((height - right_h) / 2) + 40

        for line in right_lines:
            lw = font_big.getlength(line)
            x = half_w + (half_w - lw) / 2
            draw.text((x, y_right), line, fill=COLOR_GOLD, font=font_big)
            y_right += line_h + 8

        # ── Variant B: diagonal divider ──────────────────────────
        if variant == "B":
            # Diagonal from top-centre-left to bottom-centre-right
            x_start = half_w - 30
            x_end = half_w + 30
            draw.line(
                [(x_start, int(height * 0.10)), (x_end, int(height * 0.90))],
                fill=COLOR_WHITE,
                width=4,
            )
        else:
            # Variant A — subtle vertical separator
            draw.line(
                [(half_w, int(height * 0.20)), (half_w, int(height * 0.80))],
                fill=COLOR_WHITE,
                width=2,
            )

    else:
        # ── Fallback: single centred title ───────────────────────
        title_lines = _wrap_text(title_text, font_big, max_title)
        block_h = _text_block_height(title_lines, font_big)
        y = int((height - block_h) / 2)

        bbox_big = font_big.getbbox("Ay")
        line_h = bbox_big[3] - bbox_big[1]

        for line in title_lines:
            lw = font_big.getlength(line)
            x = (width - lw) / 2
            draw.text((x, y), line, fill=COLOR_WHITE, font=font_big)
            y += line_h + 8

    # ── Watermark ────────────────────────────────────────────────
    _add_watermark(img)

    # ── Save as high-quality JPG ─────────────────────────────────
    img.save(str(output_path), "JPEG", quality=95, subsampling=0)
    return output_path.resolve()


def generate_episode_thumbnail(
    episode_num: int,
    title: str,
    loss_text: str,
    gain_text: str,
    output_path: str | Path,
    *,
    variant: Literal["A", "B"] = "A",
    width: int = 1280,
    height: int = 720,
) -> Path:
    """High-level helper that formats an episode thumbnail.

    Builds a display title like ``'Ep 3 — Survivorship Bias'`` and
    delegates to :func:`generate_thumbnail`.

    Parameters
    ----------
    episode_num : int
        Episode number.
    title : str
        Episode title / topic.
    loss_text : str
        Left-side (red) label, e.g. ``'-30% Stock Drop'``.
    gain_text : str
        Right-side (gold) label, e.g. ``'+50% Hidden Gains'``.
    output_path : str | Path
        Destination file (saved as JPG).
    variant : ``'A'`` | ``'B'``
        Layout variant.
    width, height : int
        Thumbnail dimensions.

    Returns
    -------
    Path
        Absolute path to the saved JPG.
    """
    display_title = f"Ep {episode_num} — {title}"
    return generate_thumbnail(
        title_text=display_title,
        output_path=output_path,
        variant=variant,
        width=width,
        height=height,
        loss_text=loss_text,
        gain_text=gain_text,
    )


# ── Quick self-test ──────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    out = Path(tempfile.mkdtemp())
    print("Saving sample thumbnails to:", out)

    p = generate_thumbnail(
        "Survivorship Bias",
        out / "thumb_A.jpg",
        variant="A",
        loss_text="-30%",
        gain_text="+50%",
    )
    print(" variant A →", p)

    p = generate_thumbnail(
        "Failed Funds vs Hidden Winners",
        out / "thumb_B.jpg",
        variant="B",
    )
    print(" variant B →", p)

    p = generate_episode_thumbnail(
        episode_num=1,
        title="Survivorship Bias",
        loss_text="-30% Drop",
        gain_text="+50% Hidden",
        output_path=out / "ep1_thumb.jpg",
        variant="A",
    )
    print(" episode   →", p)

    p = generate_thumbnail(
        "One Simple Title Without VS",
        out / "thumb_single.jpg",
    )
    print(" single    →", p)

    print("\nDone ✓")
