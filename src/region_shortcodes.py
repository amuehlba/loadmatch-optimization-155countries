"""Region name (config / Fortran GRIDUSE) -> the PI's xx-file shortcode.

His 30-region post-processing program keys on ``xx.<SHORTCODE>`` filenames.
Kept in one stdlib-only module so both the GA driver (run_full_workflow.py) and
the standalone reporting (export_comparison.py) can use it without pulling in
heavy dependencies.
"""

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
