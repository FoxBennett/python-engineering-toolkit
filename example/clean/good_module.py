"""A well-behaved module used to verify the zero-findings path."""

import logging

log = logging.getLogger(__name__)


def to_int(value, default=0):
    """Parse an integer, falling back to a default."""
    try:
        return int(value)
    except (TypeError, ValueError):
        # Non-numeric input is expected from the CLI. The caller decides
        # whether the default is acceptable.
        return default


def read_config(path):
    """Read a config file, logging and re-raising on failure."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        log.exception("could not read %s", path)
        raise
