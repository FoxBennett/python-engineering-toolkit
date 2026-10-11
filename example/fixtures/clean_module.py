"""A well-behaved module."""


def parse(value):
    try:
        return int(value)
    except ValueError:
        # Blank input is expected from the CLI; treat it as zero.
        return 0
