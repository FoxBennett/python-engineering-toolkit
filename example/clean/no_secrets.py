"""Fixture: things that look like credentials but are not.

Each line below matches a naive pattern and should be filtered out, so the
scanner stays trustworthy rather than crying wolf.
"""

import os

# An environment variable NAME, not a literal value.
API_KEY = os.environ["MY_SERVICE_API_KEY"]
TOKEN = os.getenv("SOME_TOKEN")

# Obvious placeholders.
EXAMPLE_KEY = "your-api-key-here-replace-me"
PLACEHOLDER = "xxxxxxxxxxxxxxxxxxxxxxxx"

# A model identifier that happens to look like a prefixed key.
MODEL = "vosk-model-small-en-0.22"

# Short values are not treated as credentials.
SHORT = "sk-abc123"
