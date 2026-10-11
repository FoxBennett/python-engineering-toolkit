"""Deliberate suppression."""


def probe():
    try:
        import optional_dep  # noqa
    except ImportError:  # noqa: S110 - optional dependency, absence is fine
        pass
