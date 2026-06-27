"""
text_cards.py — Text Card Generator for The Bias Files

Creates full-screen text overlay images (1920×1080) using PIL/Pillow
for use as video segments in the automated YouTube pipeline.

Brand colors:
    Background : #1A1A1A (charcoal)
    Loss/danger : #E63946 (red)
    Gain/value  : #D4AF37 (gold)
    White text  : #FFFFFF
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# ── Brand palette ────────────────────────────────────────────────
BG_CHARCOAL = "#1A1A1A"
COLOR_RED = "#E63946"
COLOR_GOLD = "#D4AF37"
COLOR_WHITE = "#FFFFFF"

# ── Font paths (Windows) ────────────────────────────────────────
_FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"
_FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
_FONT_ITALIC = "C:/Windows/Fonts/ariali.ttf"
_FONT_BOLD_ITALIC = "C:/Windows/Fonts/arialbi.ttf"


# ── Helpers ──────────────────────────────────────────────────────

def _get_font(
    size: int,
    bold: bool = False,
    italic: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load an Arial TrueType font, falling back to the PIL default.

    Parameters
    ----------
    size : int
        Point size.
    bold : bool
        Use Arial Bold when *True*.
    italic : bool
        Use Arial Italic (or Bold-Italic if *bold* is also *True*).

    Returns
    -------
    ImageFont.FreeTypeFont | ImageFont.ImageFont
    """
    if bold and italic:
        candidates = [_FONT_BOLD_ITALIC, _FONT_BOLD, _FONT_ITALIC, _FONT_REGULAR]
    elif bold:
        candidates = [_FONT_BOLD, _FONT_REGULAR]
    elif italic:
        candidates = [_FONT_ITALIC, _FONT_REGULAR]
    else:
        candidates = [_FONT_REGULAR]

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue

    # Last resort — PIL bitmap default (ignores *size*, but won't crash)
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap *text* so each line fits within *max_width* pixels.

    Falls back to ``textwrap.wrap`` with a character estimate when the
    font has no ``getlength`` method (bitmap default).
    """
    if not hasattr(font, "getlength"):
        avg_char = max(max_width // 20, 1)
        return textwrap.wrap(text, width=avg_char)

    words = text.split()
    lines: list[str] = []
    current_line = ""

    for word in words:
        test = f"{current_line} {word}".strip()
        if font.getlength(test) <= max_width:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

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
    line_spacing: int = 10,
) -> int:
    """Total pixel height of a multi-line text block."""
    if not lines:
        return 0
    line_h = _get_line_height(font)
    return len(lines) * line_h + (len(lines) - 1) * line_spacing


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    color: str,
    y: int,
    canvas_width: int,
    max_text_width: int,
    line_spacing: int = 10,
) -> int:
    """Draw word-wrapped, horizontally-centred text and return the Y after
    the last line (useful for stacking blocks).
    """
    lines = _wrap_text(text, font, max_text_width)
    line_h = _get_line_height(font)

    for line in lines:
        lw = font.getlength(line)
        x = (canvas_width - lw) / 2
        draw.text((x, y), line, fill=color, font=font)
        y += line_h + line_spacing

    return y


# ── Public API ───────────────────────────────────────────────────

def create_text_card(
    text: str,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    font_size: int = 120,
    text_color: str = COLOR_WHITE,
    bg_color: str = BG_CHARCOAL,
) -> Path:
    """Create a full-screen text card with centred text.

    Ideal for dramatic reveals like ``'COMPARED TO WHAT'``.

    Parameters
    ----------
    text : str
        The message to render.
    output_path : str | Path
        Destination PNG path.
    width, height : int
        Canvas dimensions (default 1920×1080).
    font_size : int
        Font point size.
    text_color, bg_color : str
        Hex colour strings.

    Returns
    -------
    Path
        Absolute path to the saved PNG.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    font = _get_font(font_size, bold=True)

    max_tw = int(width * 0.85)
    lines = _wrap_text(text, font, max_tw)
    block_h = _text_block_height(lines, font)
    y_start = (height - block_h) / 2

    _draw_centered_text(draw, text, font, text_color, int(y_start), width, max_tw)

    img.save(str(output_path), "PNG")
    return output_path.resolve()


def create_citation_card(
    study_name: str,
    authors: str,
    journal: str,
    year: str | int,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Create an academic citation card.

    Layout (top → bottom, vertically centred as a group):
        • Study name — white, large
        • Authors     — gold, medium
        • Journal + year — white italic, small

    Returns
    -------
    Path
        Absolute path to the saved PNG.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), BG_CHARCOAL)
    draw = ImageDraw.Draw(img)

    font_title = _get_font(64, bold=True)
    font_authors = _get_font(48)
    font_journal = _get_font(36, italic=True)

    max_tw = int(width * 0.80)
    gap = 30  # vertical gap between sections

    lines_title = _wrap_text(study_name, font_title, max_tw)
    lines_authors = _wrap_text(authors, font_authors, max_tw)
    journal_line = f"{journal}, {year}"
    lines_journal = _wrap_text(journal_line, font_journal, max_tw)

    h_title = _text_block_height(lines_title, font_title)
    h_authors = _text_block_height(lines_authors, font_authors)
    h_journal = _text_block_height(lines_journal, font_journal)
    total_h = h_title + gap + h_authors + gap + h_journal

    y = int((height - total_h) / 2)
    y = _draw_centered_text(draw, study_name, font_title, COLOR_WHITE, y, width, max_tw)
    y += gap
    y = _draw_centered_text(draw, authors, font_authors, COLOR_GOLD, y, width, max_tw)
    y += gap
    _draw_centered_text(draw, journal_line, font_journal, COLOR_WHITE, y, width, max_tw)

    img.save(str(output_path), "PNG")
    return output_path.resolve()


def create_title_card(
    title: str,
    subtitle: str,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Create a channel intro / title card.

    Layout:
        • 'THE BIAS FILES' — gold, large
        • Episode title     — white, medium
        • Subtitle          — white, smaller

    Returns
    -------
    Path
        Absolute path to the saved PNG.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), BG_CHARCOAL)
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(100, bold=True)
    font_title = _get_font(64, bold=True)
    font_sub = _get_font(40)

    max_tw = int(width * 0.85)
    gap = 40

    brand = "THE BIAS FILES"
    h_brand = _text_block_height(_wrap_text(brand, font_brand, max_tw), font_brand)
    h_title = _text_block_height(_wrap_text(title, font_title, max_tw), font_title)
    h_sub = _text_block_height(_wrap_text(subtitle, font_sub, max_tw), font_sub)
    total_h = h_brand + gap + h_title + gap + h_sub

    y = int((height - total_h) / 2)
    y = _draw_centered_text(draw, brand, font_brand, COLOR_GOLD, y, width, max_tw)
    y += gap
    y = _draw_centered_text(draw, title, font_title, COLOR_WHITE, y, width, max_tw)
    y += gap
    _draw_centered_text(draw, subtitle, font_sub, COLOR_WHITE, y, width, max_tw)

    img.save(str(output_path), "PNG")
    return output_path.resolve()


def create_stat_card(
    number: str,
    label: str,
    output_path: str | Path,
    *,
    color: str = COLOR_RED,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Create a big-number statistic card.

    E.g. ``'-30%'`` in red with ``'Stock Drop'`` below in white.

    Returns
    -------
    Path
        Absolute path to the saved PNG.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), BG_CHARCOAL)
    draw = ImageDraw.Draw(img)

    font_number = _get_font(180, bold=True)
    font_label = _get_font(56)

    max_tw = int(width * 0.85)
    gap = 30

    h_num = _text_block_height(_wrap_text(number, font_number, max_tw), font_number)
    h_lbl = _text_block_height(_wrap_text(label, font_label, max_tw), font_label)
    total_h = h_num + gap + h_lbl

    y = int((height - total_h) / 2)
    y = _draw_centered_text(draw, number, font_number, color, y, width, max_tw)
    y += gap
    _draw_centered_text(draw, label, font_label, COLOR_WHITE, y, width, max_tw)

    img.save(str(output_path), "PNG")
    return output_path.resolve()


def create_vs_card(
    left_text: str,
    right_text: str,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Create a split comparison card: left in red · VS · right in gold.

    Returns
    -------
    Path
        Absolute path to the saved PNG.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), BG_CHARCOAL)
    draw = ImageDraw.Draw(img)

    font_main = _get_font(80, bold=True)
    font_vs = _get_font(60, bold=True)

    half_w = width // 2
    margin = 60
    max_side_w = half_w - margin * 2
    vs_text = "VS"

    # ── Wrap both sides ──────────────────────────────────────────
    left_lines = _wrap_text(left_text, font_main, max_side_w)
    right_lines = _wrap_text(right_text, font_main, max_side_w)

    bbox_main = font_main.getbbox("Ay")
    line_h = bbox_main[3] - bbox_main[1]
    spacing = 10

    left_block_h = len(left_lines) * line_h + (len(left_lines) - 1) * spacing
    right_block_h = len(right_lines) * line_h + (len(right_lines) - 1) * spacing
    max_block_h = max(left_block_h, right_block_h)

    # ── Draw left side (red, centred in left half) ───────────────
    y_left = int((height - left_block_h) / 2)
    for line in left_lines:
        lw = font_main.getlength(line)
        x = (half_w - lw) / 2
        draw.text((x, y_left), line, fill=COLOR_RED, font=font_main)
        y_left += line_h + spacing

    # ── Draw right side (gold, centred in right half) ────────────
    y_right = int((height - right_block_h) / 2)
    for line in right_lines:
        lw = font_main.getlength(line)
        x = half_w + (half_w - lw) / 2
        draw.text((x, y_right), line, fill=COLOR_GOLD, font=font_main)
        y_right += line_h + spacing

    # ── Vertical divider ─────────────────────────────────────────
    div_x = half_w
    div_top = int(height * 0.15)
    div_bot = int(height * 0.85)
    draw.line([(div_x, div_top), (div_x, div_bot)], fill=COLOR_WHITE, width=3)

    # ── "VS" badge centred ───────────────────────────────────────
    vs_w = font_vs.getlength(vs_text)
    vs_bbox = font_vs.getbbox(vs_text)
    vs_h = vs_bbox[3] - vs_bbox[1]
    vs_x = int((width - vs_w) / 2)
    vs_y = int((height - vs_h) / 2)

    # Draw a small dark circle behind VS for legibility
    pad = 20
    cx = int(width / 2)
    cy = int(height / 2)
    r = int(max(vs_w, vs_h) / 2) + pad
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BG_CHARCOAL)
    draw.text((vs_x, vs_y), vs_text, fill=COLOR_WHITE, font=font_vs)

    img.save(str(output_path), "PNG")
    return output_path.resolve()


# ── Quick self-test ──────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile, os

    out = Path(tempfile.mkdtemp())
    print("Saving sample cards to:", out)

    p = create_text_card("COMPARED TO WHAT", out / "text.png")
    print(" text_card       →", p)

    p = create_citation_card(
        "Survivorship Bias in Active Fund Management",
        "Brown, Goetzmann, Ibbotson & Ross",
        "Journal of Finance",
        1992,
        out / "citation.png",
    )
    print(" citation_card   →", p)

    p = create_title_card(
        "The Ship That Proved Them Wrong",
        "Episode 1",
        out / "title.png",
    )
    print(" title_card      →", p)

    p = create_stat_card("-30%", "Stock Drop", out / "stat.png")
    print(" stat_card       →", p)

    p = create_vs_card("Failed Funds", "Hidden Winners", out / "vs.png")
    print(" vs_card         →", p)

    print("\nDone ✓")
