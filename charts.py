"""
charts.py — Branded Chart Generator for The Bias Files
======================================================

Creates YouTube-ready charts (1920×1080) using Matplotlib with
The Bias Files dark-mode brand palette.

Usage:
    from charts import create_bar_chart, create_comparison_chart, create_loss_gain_chart

    create_bar_chart(
        labels=["A", "B", "C"],
        values=[10, 25, 15],
        colors=["#E63946", "#D4AF37", "#FFFFFF"],
        title="My Chart",
        output_path="output/chart.png",
    )
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for headless use
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ---------------------------------------------------------------------------
# Brand palette
# ---------------------------------------------------------------------------

BG_COLOR   = "#1A1A1A"   # charcoal background
RED        = "#E63946"    # loss / danger
GOLD       = "#D4AF37"    # gain / value
WHITE      = "#FFFFFF"    # text
GRID_COLOR = "#333333"    # subtle grid lines


def _brand_style(ax: plt.Axes, fig: plt.Figure) -> None:
    """Apply common brand styling to axes and figure."""
    fig.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    # Spine styling
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)
        spine.set_linewidth(0.8)

    # Tick styling
    ax.tick_params(colors=WHITE, labelsize=22, length=6, width=1)
    ax.xaxis.label.set_color(WHITE)
    ax.yaxis.label.set_color(WHITE)


def _save(fig: plt.Figure, output_path: str | Path) -> Path:
    """Save figure and close, returning the output Path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        str(out),
        facecolor=fig.get_facecolor(),
        dpi=100,
        bbox_inches="tight",
    )
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_bar_chart(
    labels: list[str],
    values: list[float],
    colors: list[str],
    title: str,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Create a horizontal bar chart with dark background and brand styling.

    Parameters
    ----------
    labels : list[str]
        Category labels for each bar (displayed on the y-axis).
    values : list[float]
        Numeric values for each bar.
    colors : list[str]
        Hex color for each bar. Length must match *labels*.
    title : str
        Chart title, rendered in white at the top.
    output_path : str or Path
        Where to save the PNG.
    width, height : int
        Image dimensions in pixels (at 100 dpi).

    Returns
    -------
    Path
        Path to the saved PNG file.
    """
    fig_w = width / 100
    fig_h = height / 100
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    _brand_style(ax, fig)

    y_pos = range(len(labels))
    bars = ax.barh(y_pos, values, color=colors, height=0.55, edgecolor="none")

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=26, fontweight="bold", color=WHITE)
    ax.invert_yaxis()  # top label first

    # Value labels on each bar
    max_val = max(values) if values else 1
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_width() + max_val * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{val:,.0f}" if isinstance(val, (int, float)) else str(val),
            va="center",
            ha="left",
            fontsize=24,
            fontweight="bold",
            color=WHITE,
        )

    ax.set_title(title, fontsize=34, fontweight="bold", color=WHITE, pad=20)
    ax.set_xlim(0, max_val * 1.20)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.5, alpha=0.6)

    fig.tight_layout(pad=2.0)
    return _save(fig, output_path)


def create_comparison_chart(
    label_a: str,
    value_a: float,
    label_b: str,
    value_b: float,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Side-by-side vertical bars comparing two values (loss vs gain).

    *label_a* is rendered in RED, *label_b* in GOLD by default.

    Parameters
    ----------
    label_a, label_b : str
        Display names for each bar.
    value_a, value_b : float
        Numeric values to compare.
    output_path : str or Path
        Where to save the PNG.
    width, height : int
        Image dimensions in pixels (at 100 dpi).

    Returns
    -------
    Path
        Path to the saved PNG file.
    """
    fig_w = width / 100
    fig_h = height / 100
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    _brand_style(ax, fig)

    labels = [label_a, label_b]
    values = [value_a, value_b]
    colors = [RED, GOLD]

    bars = ax.bar(labels, values, color=colors, width=0.45, edgecolor="none")

    # Value labels above each bar
    max_val = max(values) if values else 1
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max_val * 0.03,
            f"{val:,.1f}",
            ha="center",
            va="bottom",
            fontsize=36,
            fontweight="bold",
            color=WHITE,
        )

    ax.set_ylim(0, max_val * 1.30)
    ax.tick_params(axis="x", labelsize=30)
    ax.tick_params(axis="y", labelsize=22)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.5, alpha=0.6)

    # Remove bottom spine for cleaner look
    ax.spines["bottom"].set_visible(False)

    fig.tight_layout(pad=2.0)
    return _save(fig, output_path)


def create_loss_gain_chart(
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Loss-Aversion 2:1 ratio chart for Episode 1.

    Shows the psychological finding that losses hurt ~2× more than
    equivalent gains feel good.  The "Pain of Losing $100" bar (red)
    is twice the height of the "Joy of Gaining $100" bar (gold).

    Parameters
    ----------
    output_path : str or Path
        Where to save the PNG.
    width, height : int
        Image dimensions in pixels (at 100 dpi).

    Returns
    -------
    Path
        Path to the saved PNG file.
    """
    fig_w = width / 100
    fig_h = height / 100
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    _brand_style(ax, fig)

    labels = ["Pain of Losing $100", "Joy of Gaining $100"]
    values = [2.0, 1.0]
    colors = [RED, GOLD]

    bars = ax.bar(labels, values, color=colors, width=0.45, edgecolor="none")

    # Annotate each bar with its multiplier
    for bar, val, label_text in zip(bars, values, ["2×", "1×"]):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.08,
            label_text,
            ha="center",
            va="bottom",
            fontsize=48,
            fontweight="bold",
            color=WHITE,
        )

    ax.set_title(
        "Loss Aversion: Losses Hurt 2× More",
        fontsize=36,
        fontweight="bold",
        color=WHITE,
        pad=24,
    )

    ax.set_ylabel("Emotional Impact", fontsize=24, fontweight="bold", color=WHITE)
    ax.set_ylim(0, 2.8)
    ax.set_yticks([0, 0.5, 1.0, 1.5, 2.0])
    ax.tick_params(axis="x", labelsize=26)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.5, alpha=0.6)

    # Explanatory subtitle
    fig.text(
        0.5, 0.02,
        "Kahneman & Tversky — Prospect Theory (1979)",
        ha="center",
        fontsize=18,
        fontstyle="italic",
        color="#888888",
    )

    fig.tight_layout(pad=2.0)
    return _save(fig, output_path)


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    out_dir = Path("output/charts")
    out_dir.mkdir(parents=True, exist_ok=True)

    p1 = create_bar_chart(
        labels=["Stocks", "Real Estate", "Crypto", "Bonds"],
        values=[12000, 8500, 15000, 4200],
        colors=[GOLD, GOLD, RED, GOLD],
        title="Investment Returns",
        output_path=out_dir / "bar_demo.png",
    )
    print(f"  ✓ bar_chart        → {p1}")

    p2 = create_comparison_chart(
        label_a="Loss",
        value_a=2.0,
        label_b="Gain",
        value_b=1.0,
        output_path=out_dir / "comparison_demo.png",
    )
    print(f"  ✓ comparison_chart → {p2}")

    p3 = create_loss_gain_chart(out_dir / "loss_gain_demo.png")
    print(f"  ✓ loss_gain_chart  → {p3}")

    print("Done.")
