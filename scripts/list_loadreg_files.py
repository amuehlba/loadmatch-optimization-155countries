"""List the ELECMAPS loadreg.* files actually needed for the 30-region run.

The Fortran (IFNEWLOAD=1, Electricity Maps 2024) reads one hourly-load file per
member country of each grid region: ``loadreg.<COUNTRY>`` where <COUNTRY> is the
country name from countrystats.dat (column 1) TRUNCATED to 14 characters (the
Fortran's CHARACTER(14) NAMORIGGR).  Example: 'UNITED-STATES-OF-AMERICA' →
'loadreg.UNITED-STATES-'.

So the files you need = one loadreg.<country> per country in countrystats.dat
(155 total), plus the Electricity Maps master '24LOADELECMAPS.dat'.  Other files
in a full ELECMAPS folder (loadreg.CONUS*, US-state grids, CAISO, Canary
islands, loadreg.COUNTRY2030GW) belong to grids not used by the 30 regions.

Usage
-----
    # print the needed filenames (one per line) + per-region grouping
    python -m scripts.list_loadreg_files

    # also check which of them are present / missing in your ELECMAPS folder,
    # and which files there are NOT needed (candidates to skip)
    python -m scripts.list_loadreg_files --elecmaps-dir /path/to/ELECMAPS

    # emit just the bare filenames (for scripting: rsync/cp a subset)
    python -m scripts.list_loadreg_files --names-only
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
COUNTRYSTATS = REPO_ROOT / "data" / "raw" / "countrystats.dat"
NAME_LEN = 14  # Fortran CHARACTER(14) NAMORIGGR / NAMEORIG

# Extra non-loadreg input the Electricity Maps path also uses.
MASTER_FILE = "24LOADELECMAPS.dat"


def loadreg_name(country: str) -> str:
    """loadreg filename for a country: name truncated to 14 chars (as the Fortran does)."""
    return "loadreg." + country[:NAME_LEN]


def parse_countrystats(path: Path) -> List[tuple]:
    """Return [(country, region), ...] from countrystats.dat (tab-separated, 3 header lines)."""
    rows = []
    text = path.read_text(encoding="ascii", errors="replace")
    for line in text.splitlines():
        if "\t" not in line:
            continue
        parts = line.split("\t")
        country, region = parts[0].strip(), parts[1].strip()
        # Skip header rows: col2 must look like a region (upper-case, no spaces),
        # and the header row literally has "GRID-REGION".
        if not country or region in ("", "GRID-REGION"):
            continue
        rows.append((country, region))
    return rows


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--countrystats", type=Path, default=COUNTRYSTATS,
                        help="Path to countrystats.dat (default: %(default)s).")
    parser.add_argument("--elecmaps-dir", type=Path, default=None,
                        help="If given, report present/missing/extra against this folder.")
    parser.add_argument("--names-only", action="store_true",
                        help="Print only the bare needed filenames, one per line.")
    args = parser.parse_args(argv)

    if not args.countrystats.exists():
        sys.exit(f"countrystats.dat not found at {args.countrystats} "
                 f"(gunzip it into data/raw/ first).")

    rows = parse_countrystats(args.countrystats)
    by_region: Dict[str, List[str]] = {}
    needed = set()
    for country, region in rows:
        fname = loadreg_name(country)
        needed.add(fname)
        by_region.setdefault(region, []).append("{}  ({})".format(fname, country))
    needed_all = sorted(needed) + [MASTER_FILE]

    if args.names_only:
        for n in needed_all:
            print(n)
        return

    print(f"Needed ELECMAPS files: {len(needed)} per-country loadreg files "
          f"(+ {MASTER_FILE}) for {len(rows)} countries in {len(by_region)} regions.\n")
    for region in sorted(by_region):
        files = by_region[region]
        print(f"  {region}  ({len(files)}):")
        for f in sorted(files):
            print(f"      {f}")
    print(f"\n  + {MASTER_FILE}   (Electricity Maps master)")

    if args.elecmaps_dir:
        d = args.elecmaps_dir
        if not d.is_dir():
            sys.exit(f"\n--elecmaps-dir not a directory: {d}")
        present = {p.name for p in d.iterdir() if p.is_file()}
        need = set(needed_all)
        missing = sorted(need - present)
        extra = sorted(present - need)
        print(f"\n=== Check against {d} ===")
        print(f"  present & needed : {len(need & present)} / {len(need)}")
        if missing:
            print(f"  MISSING ({len(missing)}), download these:")
            for m in missing:
                print(f"      {m}")
        else:
            print("  MISSING: none, you have everything needed.")
        if extra:
            print(f"  not needed ({len(extra)}), safe to skip (other grids):")
            for e in extra:
                print(f"      {e}")


if __name__ == "__main__":
    main()
