"""Shared publication style + palette for the comparison figures.

Landscape orientation, sans-serif, larger fonts, and a colour-blind-safe palette
validated with the data-viz "six checks" (Machado-2009 CVD deltaE, OKLCH
lightness/chroma, WCAG contrast on a white print surface):

  * comparison entities  blue <-> orange, worst-pair CVD deltaE 96.7  (all pass)
  * data-centre 5-set     green/blue/violet/amber/red, worst-pair deltaE 13.3
                          (one green<->amber pair sits in the 8-12 floor band and
                          is carried by a second encoding channel: marker shape)

Colour follows the entity across every figure: trial-and-error is the neutral
grey reference, the GA-from-trial-and-error result is blue, the GA-from-scratch
run is orange.  Text never wears a series colour.

Fonts are TrueType-embedded (pdf.fonttype 42) so vector PDFs stay editable for a
journal production pipeline.
"""
import matplotlib as mpl

# --- ink / chrome (text and axes never wear a series colour) ------------------
INK = "#1a1a1a"
INK_SECONDARY = "#4a4a48"
MUTED = "#7a7f85"          # axis lines, reference rules
GRID = "#e6e6e3"           # hairline gridlines
STICK = "#c7c9cc"          # dumbbell connector
BAND = "#f5f5f3"           # alternating row/column banding

# --- comparison-figure entities (cost / land / solve time) --------------------
C_BASELINE = "#6e7377"     # trial-and-error  (neutral grey reference)
C_GA = "#2a78d6"           # GA from trial-and-error  (headline result)
C_SCRATCH = "#eb6834"      # GA from scratch, extended  (context)
LABEL_BASELINE = "Trial-and-error"
LABEL_GA = "GA (from trial-and-error)"
LABEL_SCRATCH = "GA (from scratch, extended)"

# --- data-centre supply strategies (colour + marker = composite encoding) -----
DC_STRATEGIES = [
    ("dc1",    "EGS (case 1)",                                    "#008300", "o"),
    ("dc2",    "Utility PV + wind + batteries + H$_2$ (case 2)",  "#2a78d6", "s"),
    ("dc2rc",  "Rooftop PV + batteries + H$_2$ (case 3)",         "#4a3aa7", "^"),
    ("dc2bat", "Case 2, batteries only",                          "#c98500", "D"),
    ("dc2h2",  "Case 2, hydrogen only",                           "#e34948", "v"),
]


# --- diverging scale (polarity: below / above a no-change baseline) -----------
# blue (negative) <-> neutral <-> red (positive): the data-viz diverging rule
# (two hues + a neutral midpoint; blue and red read as opposite, the midpoint
# reads as "nothing").  Reused wherever a signed change is shown on a heatmap.
C_DIV_NEG = C_GA            # blue
C_DIV_POS = "#e34948"       # red (palette slot 6)
C_DIV_MID = "#f2f2f0"       # near-neutral surface


def diverging_cmap():
    """Blue->neutral->red colormap for signed-change heatmaps."""
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(
        "ga_diverge", [C_DIV_NEG, C_DIV_MID, C_DIV_POS])


def apply_style() -> None:
    """Apply the shared rcParams.  Call once at import time in each figure script."""
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "Helvetica Neue", "DejaVu Sans"],
        "font.size": 12,
        "axes.titlesize": 13,
        "axes.labelsize": 13,
        "axes.labelcolor": INK,
        "axes.edgecolor": MUTED,
        "axes.linewidth": 0.9,
        "axes.axisbelow": True,
        "text.color": INK,
        "xtick.color": INK_SECONDARY,
        "ytick.color": INK_SECONDARY,
        "xtick.labelsize": 10.5,
        "ytick.labelsize": 11,
        "xtick.major.width": 0.9,
        "ytick.major.width": 0.9,
        "legend.fontsize": 11,
        "legend.frameon": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
        "savefig.dpi": 400,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
    })


def region_order(df, by="baseline_cost_bil_per_yr", ascending=False):
    """Region order shared across figures: largest system cost on the left.

    Keeps the x-axis identical in every figure so a reader can track a region
    across the cost, land, and solve-time panels.
    """
    d = df[df[by].notna()]
    return list(d.sort_values(by, ascending=ascending)["region"])


def add_region_bands(ax, n):
    """Alternating grey/white vertical banding, one band per region column.

    Groups the marks belonging to a region and ties them to the x tick labels;
    on a shared-x multi-panel figure it also lets the reader line up a region
    across panels.  Drawn behind everything (zorder 0).
    """
    for i in range(n):
        if i % 2 == 0:
            ax.axvspan(i - 0.5, i + 0.5, color=BAND, zorder=0)


def style_region_axis(ax, regions, rotation=45):
    """Put the region names on the x-axis with consistent, legible rotation."""
    ax.set_xticks(range(len(regions)))
    ax.set_xticklabels(regions, rotation=rotation, ha="right",
                        rotation_mode="anchor")
    ax.set_xlim(-0.7, len(regions) - 0.3)
    ax.tick_params(axis="x", length=0)
