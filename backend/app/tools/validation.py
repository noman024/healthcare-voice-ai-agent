from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any


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

# Bangladesh national mobile with trunk 0 (operators 013–019), 11 digits total.
_BD_MOBILE_NATIONAL_RE = re.compile(r"^0(13|14|15|16|17|18|19)\d{8}$")

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
    lead = s.lstrip()
    digits = re.sub(r"\D", "", s)
    promoted_e164 = False
    default_cc = os.getenv("PHONE_DEFAULT_CC", "").strip().lower()

    if not lead.startswith("+"):
        # Bangladesh +880 when deployment expects national 01[3-9]… mobiles (no country code in speech).
        if default_cc in ("880", "bd", "bd880", "bangladesh") and _BD_MOBILE_NATIONAL_RE.fullmatch(digits):
            digits = "880" + digits[1:]
            promoted_e164 = True
        # UK national mobile 07… (11 digits). Skipped when PHONE_DEFAULT_CC is BD so +44 is not misapplied.
        elif (
            default_cc not in ("880", "bd", "bd880", "bangladesh")
            and len(digits) == 11
            and digits.startswith("07")
        ):
            digits = "44" + digits[1:]
            promoted_e164 = True
    n = len(digits)
    if n < PHONE_MIN_DIGITS or n > PHONE_MAX_DIGITS:
        raise ToolValidationError(
            f"Phone must be {PHONE_MIN_DIGITS}-{PHONE_MAX_DIGITS} digits "
            "(ITU/E.164 style). Include country code (e.g. +44…) when not all digits are NANP/US-length.",
            field="phone",
        )
    if lead.startswith("+") or promoted_e164:
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


def calendar_today() -> date:
    """Local-date 'today' for appointment rules (monkeypatch in tests)."""
    return date.today()


def assert_date_not_in_past(date_iso: str, *, field: str = "date") -> None:
    """Reject appointment/slot dates strictly before today (today and future allowed)."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    t = calendar_today()
    if d < t:
        raise ToolValidationError(
            f"This date ({date_iso}) is before today ({t.isoformat()}). "
            "Use today or a future day.",
            field=field,
        )


def parse_time_str(value: str) -> str:
    s = value.strip()
    m = _TIME_RE.match(s)
    if not m:
        raise ToolValidationError("Time must be HH:MM (24-hour).", field="time")
    return f"{m.group(1)}:{m.group(2)}"


_BOOKING_NAME_BLOCKLIST = frozenset(
    {
        "unknown",
        "nothing",
        "none",
        "n/a",
        "na",
        "test",
        "user",
        "me",
        "myself",
        "someone",
        "anybody",
        "nobody",
        "anonymous",
        "idk",
        "nope",
    }
)

_NAME_JOKE_NO_PATTERN = re.compile(r"(?i)no\.?\d*")


def validate_booking_display_name(raw: str) -> str:
    """Reject placeholder / garbage names for book_appointment."""
    s = str(raw or "").strip()
    if len(s) < 2:
        raise ToolValidationError("Name must be at least 2 characters.", field="name")
    low = s.lower()
    if low in _BOOKING_NAME_BLOCKLIST:
        raise ToolValidationError(
            "Please provide a real full name, not a placeholder.",
            field="name",
        )
    if not any(ch.isalpha() for ch in s):
        raise ToolValidationError("Name should include at least one letter.", field="name")
    if _NAME_JOKE_NO_PATTERN.fullmatch(s):
        raise ToolValidationError("Please provide your real name.", field="name")
    return s


def person_name_precheck_ok(raw: Any) -> bool:
    try:
        validate_booking_display_name(str(raw or ""))
        return True
    except ToolValidationError:
        return False


def validate_clinic_template_time(time_hhmm: str) -> None:
    """Times must fall on the configured slot grid (e.g. 09:00–16:30 when close hour is 17)."""
    from app.tools import slots as slots_mod

    allowed = slots_mod.day_slot_candidates()
    if time_hhmm not in allowed:
        hint = f"{allowed[0]}–{allowed[-1]}" if allowed else "—"
        raise ToolValidationError(
            f"Time {time_hhmm} is not a clinic slot. Bookable grid is {len(allowed)} half-hour steps ({hint}). "
            "Use fetch_slots and pick a listed time.",
            field="time",
        )


def require_int(args: dict, key: str) -> int:
    v = args.get(key)
    if v is None:
        raise ToolValidationError(f"Missing '{key}'.", field=key)
    try:
        return int(v)
    except (TypeError, ValueError) as e:
        raise ToolValidationError(f"'{key}' must be an integer.", field=key) from e
