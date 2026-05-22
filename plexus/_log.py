import os
import sys

_QUIET = os.environ.get("PLEXUS_QUIET", "").lower() in ("1", "true", "yes")


def _say(line: str) -> None:
    """Single-line status message to stderr. Skipped if PLEXUS_QUIET=1."""
    if _QUIET:
        return
    try:
        sys.stderr.write(f"[plexus] {line}\n")
        sys.stderr.flush()
    except Exception:
        pass
