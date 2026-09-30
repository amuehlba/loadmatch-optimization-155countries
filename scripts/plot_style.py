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
# Settled paper naming (2026-09): the PI's model is LOADMATCH, the optimised
# model LOADMATCH-O; the cases Trial-and-Error / Baseline / Scratch are italic.
# Italic is rendered via mathtext (\mathit); apply_style() points mathtext at
# the sans-serif text font so the italic word matches the surrounding label.
# Hyphens stay outside the math: in mathtext "-" is a minus sign.
TAE_IT = r"$\mathit{Trial}$-$\mathit{and}$-$\mathit{Error}$"
LABEL_BASELINE = f"LOADMATCH ({TAE_IT})"
LABEL_GA = r"LOADMATCH-O ($\mathit{Baseline}$)"
LABEL_SCRATCH = r"LOADMATCH-O ($\mathit{Scratch}$)"


def plain_label(label):
    """A LABEL_* string without its mathtext markup, for stdout captions."""
    return label.replace(r"$\mathit{", "").replace("}$", "")

# --- data-centre supply cases (colour + marker = composite encoding) ----------
# Order and short labels per the PI (2026-07-18): EGS, WSBH, WSB, WSH, RBH.
# Each case is defined in the caption/text, so the figures use only the codes.
DC_STRATEGIES = [
    ("dc1",    "DC EGS",  "#008300", "o"),
    ("dc2",    "DC WSBH", "#2a78d6", "s"),
    ("dc2bat", "DC WSB",  "#c98500", "D"),
    ("dc2h2",  "DC WSH",  "#e34948", "v"),
    ("dc2rc",  "DC RBH",  "#4a3aa7", "^"),
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
        # mathtext in the text font, so \mathit{Baseline/Scratch} is Arial Italic
        # (DejaVu Sans where Arial is absent, e.g. Sherlock)
        "mathtext.fontset": "custom",
        "mathtext.rm": "sans",
        "mathtext.it": "sans:italic",
        "mathtext.bf": "sans:bold",
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


def apply_dc_style():
    """DC-paper house style (PI request 2026-07-18): same base as apply_style()
    but Times New Roman serif for all text, including math (H2 subscripts,
    arrows).  ONLY the data-center figures call this; the baseline-optimization
    figures keep apply_style()."""
    apply_style()
    mpl.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "mathtext.fontset": "stix",      # Times-compatible math glyphs
    })


def cap_region(name):
    """PI request: capitalize only the first letter of a country/region name
    (e.g. 'AFRICA-EAST' -> 'Africa-east')."""
    return str(name).capitalize()


def region_sort_key(name):
    """Canonical alphabetical sort key for a region, robust to the spelling and
    formatting differences between data sources (case, spaces, hyphens) so every
    figure orders the 30 regions IDENTICALLY -- e.g. 'SOUTHAM-NW' and
    'South Am-NW' both key to 'southamnw'."""
    return "".join(c for c in str(name).lower() if c.isalnum())


# Display names for the 30 regions in the baseline-optimization figures, keyed
# by the model's region code.  Chosen for readability and to match the paper
# text (e.g. 'East Africa', not 'AFRICA-EAST' or 'Africa-East').  The DC figures
# keep cap_region() (PI request 2026-07-18).
REGION_LABELS = {
    "AFRICA-EAST": "East Africa",       "AFRICA-NORTH": "North Africa",
    "AFRICA-SOUTH": "Southern Africa",  "AFRICA-WEST": "West Africa",
    "AUSTRALIA": "Australia",           "CANADA": "Canada",
    "CENTRAL-AMERIC": "Central America", "CENTRAL-ASIA": "Central Asia",
    "CHINA": "China",                   "CUBA": "Cuba",
    "EUROPE": "Europe",                 "GREENLAND": "Greenland",
    "HAITI": "Haiti",                   "ICELAND": "Iceland",
    "INDIA": "India",                   "ISRAEL": "Israel",
    "JAMAICA": "Jamaica",               "JAPAN": "Japan",
    "MADAGASCAR": "Madagascar",         "MAURITIUS": "Mauritius",
    "MIDEAST": "Middle East",           "NEW-ZEALAND": "New Zealand",
    "PHILIPPINES": "Philippines",       "RUSSIA": "Russia",
    "SOUTHAM-NW": "NW South America",   "SOUTHAM-SE": "SE South America",
    "SOUTHEAST-ASIA": "Southeast Asia", "SOUTH-KOREA": "South Korea",
    "TAIWAN": "Taiwan",                 "UNITED-STATES": "United States",
}
# Spellings in the PI's Tables that do not reduce to the model code's key.
_REGION_ALIASES = {"middleeast": "MIDEAST", "centralamerica": "CENTRAL-AMERIC"}
_LABEL_BY_KEY = {region_sort_key(k): v for k, v in REGION_LABELS.items()}
_LABEL_BY_KEY.update({k: REGION_LABELS[c] for k, c in _REGION_ALIASES.items()})


def region_label(name):
    """Display name for a region given its model code ('SOUTHAM-SE') or the
    PI's Tables spelling ('South Am-SE'); unknown names pass through."""
    return _LABEL_BY_KEY.get(region_sort_key(name), str(name))


def minor_ticks(ax, x=False, y=True):
    """PI request: minor tick marks between the major numbers.  Numeric axes
    only (skip the categorical case/region axis)."""
    from matplotlib.ticker import AutoMinorLocator
    if y:
        ax.yaxis.set_minor_locator(AutoMinorLocator())
    if x:
        ax.xaxis.set_minor_locator(AutoMinorLocator())
    ax.tick_params(which="minor", length=2.5, color=MUTED)


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
