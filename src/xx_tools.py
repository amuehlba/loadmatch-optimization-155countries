"""Helpers for producing xx deliverable files (stdlib only).

The Fortran binary writes its xx report to stdout.  Our appended
READ_FACTOR_OVERRIDES subroutine echoes 'APPLIED FACTOR OVERRIDES FOR <region>'
plus one line per factor into the same stream, so a run made with a factor file
carries that block embedded in the xx content.  The PI's pristine xx files do
not contain it, and his 30-region post-processing program consumes these files,
so deliverables must match his format exactly.
"""
import re

_OVERRIDE_ECHO_START = "APPLIED FACTOR OVERRIDES FOR"
_OVERRIDE_ECHO_LINE = re.compile(r"^  [A-Z0-9]+ +=")


def strip_override_echo(stdout: str) -> str:
    """Remove the READ_FACTOR_OVERRIDES echo block from an xx output stream."""
    out = []
    skipping = False
    for line in stdout.split("\n"):
        if line.startswith(_OVERRIDE_ECHO_START):
            skipping = True
            continue
        if skipping:
            if _OVERRIDE_ECHO_LINE.match(line):
                continue
            skipping = False
        out.append(line)
    return "\n".join(out)
