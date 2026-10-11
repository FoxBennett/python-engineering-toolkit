"""Fixture: a file that should be reported as containing a credential.

The values below are synthetic and deliberately non-functional. They exist so
the scanner has something to find; if this file ever reports a real key, the
scanner is doing its job.
"""

import os

# Reported: long, mixed case, assigned to a credential-shaped name.
API_KEY = "sk-FAKE1234567890abcdefGHIJKL"

# Reported: bearer token shape.
HEADERS = {"Authorization": "Bearer FAKE-abcdef1234567890XYZ"}

# Reported: a password literal.
DB_PASSWORD = "FAKEp4ssw0rdAbCdEf123456"
