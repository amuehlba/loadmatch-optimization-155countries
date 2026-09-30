"""Helpers for producing xx report files (stdlib only).

The Fortran binary writes its xx report to stdout.  The appended
READ_FACTOR_OVERRIDES subroutine echoes 'APPLIED FACTOR OVERRIDES FOR <region>'
plus one line per factor into the same stream.  The LOADMATCH post-processing
tables expect the standard report without that block, so it is stripped before
an xx file is written.
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
