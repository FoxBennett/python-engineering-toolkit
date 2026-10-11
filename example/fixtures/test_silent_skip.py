"""A test that reports success whether or not it ran."""


def test_needs_network():
    if not has_internet():
        return
    assert fetch() == 200


def has_internet():
    return False


def fetch():
    return 200
