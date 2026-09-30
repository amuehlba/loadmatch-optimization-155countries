"""Region list and xx-file short codes (stdlib only).

The 30 region names match the Fortran GRIDUSE names in powerworld.f.  The xx
report of each region is named ``xx.<SHORTCODE>``, the convention expected by
the LOADMATCH post-processing tables.
"""
import re
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config" / "workflow.yaml"

REGION_SHORTCODE = {
    "AFRICA-EAST": "AFRICAE",   "AFRICA-NORTH": "AFRICAN",  "AFRICA-SOUTH": "AFRICAS",
    "AFRICA-WEST": "AFRICAW",   "AUSTRALIA": "AUSTRALIA",   "CANADA": "CANADA",
    "CENTRAL-AMERIC": "CENAMERICA", "CENTRAL-ASIA": "CENASIA", "CHINA": "CHINA",
    "CUBA": "CUBA",             "EUROPE": "EUROPE",         "GREENLAND": "GREENLAND",
    "HAITI": "HAITI",           "ICELAND": "ICELAND",       "INDIA": "INDIA",
    "ISRAEL": "ISRAEL",         "JAMAICA": "JAMAICA",       "JAPAN": "JAPAN",
    "MADAGASCAR": "MADAGASCAR", "MAURITIUS": "MAURITIUS",   "MIDEAST": "MIDEAST",
    "NEW-ZEALAND": "NEWZEALAND", "PHILIPPINES": "PHILIPPINES", "RUSSIA": "RUSSIA",
    "SOUTHAM-NW": "SOUTHAMNW",  "SOUTHAM-SE": "SOUTHAMSE",  "SOUTHEAST-ASIA": "SEASIA",
    "SOUTH-KOREA": "SKOREA",    "TAIWAN": "TAIWAN",         "UNITED-STATES": "USA",
}


def config_regions(path: Path = CONFIG) -> List[str]:
    """The ``regions:`` list of config/workflow.yaml, in file order."""
    regions: List[str] = []
    in_block = False
    for line in path.read_text().splitlines():
        if re.match(r"^regions:\s*(#.*)?$", line):
            in_block = True
            continue
        if in_block:
            m = re.match(r"^\s*-\s*([A-Za-z0-9\-]+)\s*$", line)
            if m:
                regions.append(m.group(1))
            elif line.strip() and not line[0].isspace():
                break
    return regions
