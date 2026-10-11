"""Handlers that swallow errors."""


def load(path):
    try:
        return open(path).read()
    except OSError:
        pass


def save(path, data):
    try:
        open(path, "w").write(data)
    except OSError:
        return
