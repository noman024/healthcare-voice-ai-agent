from __future__ import annotations

import re
from datetime import datetime


class ToolValidationError(Exception):
    def __init__(self, message: str, *, field: str | None = None):
        self.field = field
        super().__init__(message)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Subscriber digits after stripping separators: ITU E.164 allows up to 15;
# lengths vary globally (country code + national). Reject NANP-only "10 digits" mentality.
PHONE_MIN_DIGITS = 8
PHONE_MAX_DIGITS = 15

def require_str(args: dict, key: str) -> str:
    v = args.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ToolValidationError(f"Missing or empty '{key}'.", field=key)
    return str(v).strip()


def optional_str(args: dict, key: str) -> str | None:
    v = args.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def normalize_phone(raw: str) -> str:
    """Strip separators; preserve leading + for E.164-style storage. Validates digit count globally."""
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    n = len(digits)
    if n < PHONE_MIN_DIGITS or n > PHONE_MAX_DIGITS:
        raise ToolValidationError(
            f"Phone must be {PHONE_MIN_DIGITS}-{PHONE_MAX_DIGITS} digits "
            "(ITU/E.164 style). Include country code (e.g. +44…) when not all digits are NANP/US-length.",
            field="phone",
        )
    lead = s.lstrip()
    if lead.startswith("+"):
        return f"+{digits}"
    return digits


def parse_date_str(value: str) -> str:
    s = value.strip()
    if any(c.isalpha() for c in s):
        raise ToolValidationError(
            "Date must be a real calendar date in YYYY-MM-DD form (digits only); "
            "do not use placeholders like YYYY-MM-DD spelled with letters.",
            field="date",
        )
    if not _DATE_RE.match(s):
        raise ToolValidationError("Date must be YYYY-MM-DD.", field="date")
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise ToolValidationError("Invalid calendar date.", field="date") from e
    return s


def parse_time_str(value: str) -> str:
    s = value.strip()
    m = _TIME_RE.match(s)
    if not m:
        raise ToolValidationError("Time must be HH:MM (24-hour).", field="time")
    return f"{m.group(1)}:{m.group(2)}"


def require_int(args: dict, key: str) -> int:
    v = args.get(key)
    if v is None:
        raise ToolValidationError(f"Missing '{key}'.", field=key)
    try:
        return int(v)
    except (TypeError, ValueError) as e:
        raise ToolValidationError(f"'{key}' must be an integer.", field=key) from e
