"""
Synthetic default value generation for replay params the caller didn't supply.

The point: replaying a capability shouldn't require re-typing every field a
form happened to have (name, address, SSN, phone...) just to exercise the
flow again. These are deliberately FABRICATED, plausible-looking test values,
generated from the param NAME alone via simple keyword matching — never
derived from any real run, any stored user data, or anything that would
reintroduce the redaction concerns the rest of this system cares about (see
loop.py's _strip_for_persistence, which is exactly why there's no "remember
what was typed last time" option here — that would mean persisting real
typed values somewhere, which is the thing we deliberately avoid).

A param the caller DOES supply always wins; this only fills genuine gaps. See
replay.py's `auto_fill_missing_params` (defaults to True) and
ReplayResult.auto_filled_params, which reports exactly which param names were
fabricated so nothing here is a silent, invisible substitution.

This is a best-effort test-data generator, not a fixture recorder. Keyword
matching is intentionally simple and biased toward this project's actual test
surface (bank registration/login forms) — extend the table below for other
domains rather than trying to be universally clever.
"""

from __future__ import annotations

import random
import re
import string


def _random_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def generate_default(param_name: str) -> str:
    """Best-effort synthetic value for a param, guessed from its name."""
    name = param_name.lower()
    if "username" in name:
        return f"testuser_{_random_suffix()}"
    if "email" in name:
        return f"test.user+{_random_suffix()}@example.com"

    if "password" in name or "pwd" in name:
        return "TestPass123!"
    if "ssn" in name or "social_security" in name:
        return f"{random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(1000, 9999)}"
    if "phone" in name:
        return f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}"
    if "zip" in name or "postal" in name:
        return f"{random.randint(10000, 99999)}"
    if "state" in name:
        return "IL"
    if "city" in name:
        return "Springfield"
    if "street" in name or ("address" in name and "email" not in name):
        return f"{random.randint(100, 999)} Main St"
    if "first_name" in name or name == "firstname":
        return "John"
    if "last_name" in name or name == "lastname":
        return "Doe"
    if "name" in name:  # generic fallback for a bare "name" param
        return "John Doe"
    if "amount" in name or "balance" in name:
        return "100.00"
    if "date" in name:
        return "2026-01-01"
    if re.search(r"\bid\b", name):
        return "12345"

    # Generic fallback: obviously a placeholder, still non-empty.
    return f"test_{_random_suffix(4)}"
