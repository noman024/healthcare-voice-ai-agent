from __future__ import annotations

import sqlite3
from dataclasses import dataclass


class DoubleBookingError(Exception):
    """The requested slot already has a booked appointment."""

class AppointmentNotFoundError(Exception):
    """No appointment matches the given id (and optional phone)."""

class AppointmentConflictError(Exception):
    """Target slot is not available for modify/rebook."""

@dataclass(frozen=True)
class Appointment:
    id: int
    name: str
    phone: str
    date: str
    time: str
    status: str
    created_at: str


def _row_to_appt(row: sqlite3.Row) -> Appointment:
    return Appointment(
        id=int(row["id"]),
        name=str(row["name"]),
        phone=str(row["phone"]),
        date=str(row["date"]),
        time=str(row["time"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
    )


def get_appointment_by_id(conn: sqlite3.Connection, appointment_id: int) -> Appointment | None:
    cur = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
    row = cur.fetchone()
    return _row_to_appt(row) if row else None


def get_slot_occupancy(
    conn: sqlite3.Connection,
    date: str,
    time: str,
) -> str | None:
    """Return 'booked', 'cancelled', or None if the slot has no row."""
    cur = conn.execute(
        "SELECT status FROM appointments WHERE date = ? AND time = ?",
        (date, time),
    )
    row = cur.fetchone()
    return str(row["status"]) if row else None


def list_bookable_slot_times(
    conn: sqlite3.Connection,
    date: str,
    candidate_times: list[str],
) -> list[str]:
    """Slots that can be booked: empty or only a cancelled row at that time."""
    bookable: list[str] = []
    for t in candidate_times:
        occ = get_slot_occupancy(conn, date, t)
        if occ is None or occ == "cancelled":
            bookable.append(t)
    return bookable


def list_appointments_for_phone(
    conn: sqlite3.Connection,
    phone: str,
    *,
    include_cancelled: bool = False,
) -> list[Appointment]:
    if include_cancelled:
        cur = conn.execute(
            "SELECT * FROM appointments WHERE phone = ? ORDER BY date, time, id",
            (phone,),
        )
    else:
        cur = conn.execute(
            """
            SELECT * FROM appointments
            WHERE phone = ? AND status = 'booked'
            ORDER BY date, time, id
            """,
            (phone,),
        )
    return [_row_to_appt(r) for r in cur.fetchall()]


def book_appointment(
    conn: sqlite3.Connection,
    *,
    name: str,
    phone: str,
    date: str,
    time: str,
) -> Appointment:
    cur = conn.execute(
        "SELECT id, status FROM appointments WHERE date = ? AND time = ?",
        (date, time),
    )
    row = cur.fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO appointments (name, phone, date, time, status)
            VALUES (?, ?, ?, ?, 'booked')
            """,
            (name, phone, date, time),
        )
        conn.commit()
        cur2 = conn.execute(
            "SELECT * FROM appointments WHERE date = ? AND time = ? AND status = 'booked'",
            (date, time),
        )
        got = cur2.fetchone()
        assert got is not None
        return _row_to_appt(got)
    if row["status"] == "cancelled":
        conn.execute(
            """
            UPDATE appointments
            SET name = ?, phone = ?, status = 'booked'
            WHERE id = ?
            """,
            (name, phone, int(row["id"])),
        )
        conn.commit()
        cur2 = conn.execute("SELECT * FROM appointments WHERE id = ?", (int(row["id"]),))
        got = cur2.fetchone()
        assert got is not None
        return _row_to_appt(got)
    raise DoubleBookingError(f"Slot {date} {time} is already booked (appointment id {row['id']}).")


def cancel_appointment(
    conn: sqlite3.Connection,
    appointment_id: int,
    *,
    phone: str,
) -> Appointment:
    cur = conn.execute(
        "SELECT * FROM appointments WHERE id = ? AND phone = ?",
        (appointment_id, phone),
    )
    row = cur.fetchone()
    if row is None:
        raise AppointmentNotFoundError(f"No appointment {appointment_id} for phone {phone}.")
    if row["status"] == "cancelled":
        return _row_to_appt(row)
    conn.execute(
        "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
        (appointment_id,),
    )
    conn.commit()
    cur2 = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
    got = cur2.fetchone()
    assert got is not None
    return _row_to_appt(got)


def modify_appointment_timeslot(
    conn: sqlite3.Connection,
    appointment_id: int,
    *,
    phone: str,
    new_date: str,
    new_time: str,
) -> Appointment:
    cur = conn.execute(
        "SELECT * FROM appointments WHERE id = ? AND phone = ?",
        (appointment_id, phone),
    )
    row = cur.fetchone()
    if row is None:
        raise AppointmentNotFoundError(f"No appointment {appointment_id} for phone {phone}.")
    if row["status"] != "booked":
        raise AppointmentConflictError("Cannot modify a cancelled appointment; book a new slot instead.")
    old_date, old_time = str(row["date"]), str(row["time"])
    if old_date == new_date and old_time == new_time:
        return _row_to_appt(row)

    target = conn.execute(
        "SELECT id, status FROM appointments WHERE date = ? AND time = ?",
        (new_date, new_time),
    ).fetchone()

    if target is None:
        conn.execute(
            "UPDATE appointments SET date = ?, time = ? WHERE id = ?",
            (new_date, new_time, appointment_id),
        )
        conn.commit()
    elif int(target["id"]) == appointment_id:
        return _row_to_appt(row)
    elif target["status"] == "cancelled":
        conn.execute("DELETE FROM appointments WHERE id = ?", (int(target["id"]),))
        conn.execute(
            "UPDATE appointments SET date = ?, time = ? WHERE id = ?",
            (new_date, new_time, appointment_id),
        )
        conn.commit()
    else:
        raise AppointmentConflictError(
            f"Cannot move to {new_date} {new_time}: slot is already booked (id {target['id']}).",
        )

    cur2 = conn.execute("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
    got = cur2.fetchone()
    assert got is not None
    return _row_to_appt(got)
