from __future__ import annotations

import pytest

from app.db.appointments import (
    AppointmentConflictError,
    DoubleBookingError,
    book_appointment,
    cancel_appointment,
    list_appointments_for_phone,
    modify_appointment_timeslot,
)


def test_book_and_list(db_conn):
    a = book_appointment(
        db_conn,
        name="Ada Lovelace",
        phone="+15550001",
        date="2026-05-10",
        time="09:30",
    )
    assert a.status == "booked"
    rows = list_appointments_for_phone(db_conn, "+15550001")
    assert len(rows) == 1
    assert rows[0].id == a.id


def test_double_booking_same_slot(db_conn):
    book_appointment(
        db_conn,
        name="First",
        phone="+15550001",
        date="2026-05-10",
        time="10:00",
    )
    with pytest.raises(DoubleBookingError):
        book_appointment(
            db_conn,
            name="Second",
            phone="+15550002",
            date="2026-05-10",
            time="10:00",
        )


def test_cancel_then_rebook_same_slot(db_conn):
    a = book_appointment(
        db_conn,
        name="First",
        phone="+15550001",
        date="2026-05-11",
        time="11:00",
    )
    cancel_appointment(db_conn, a.id, phone="+15550001")
    b = book_appointment(
        db_conn,
        name="Second",
        phone="+15550002",
        date="2026-05-11",
        time="11:00",
    )
    assert b.id == a.id
    assert b.name == "Second"
    assert b.phone == "+15550002"
    assert b.status == "booked"


def test_modify_to_free_slot(db_conn):
    a = book_appointment(
        db_conn,
        name="Mover",
        phone="+15550003",
        date="2026-05-12",
        time="08:00",
    )
    updated = modify_appointment_timeslot(
        db_conn,
        a.id,
        phone="+15550003",
        new_date="2026-05-13",
        new_time="15:45",
    )
    assert updated.date == "2026-05-13"
    assert updated.time == "15:45"
    assert list_appointments_for_phone(db_conn, "+15550003", include_cancelled=False)


def test_modify_to_occupied_slot_fails(db_conn):
    a = book_appointment(
        db_conn,
        name="A",
        phone="+1A",
        date="2026-05-14",
        time="09:00",
    )
    book_appointment(
        db_conn,
        name="B",
        phone="+1B",
        date="2026-05-14",
        time="10:00",
    )
    with pytest.raises(AppointmentConflictError):
        modify_appointment_timeslot(
            db_conn,
            a.id,
            phone="+1A",
            new_date="2026-05-14",
            new_time="10:00",
        )


def test_modify_reuses_cancelled_target_slot(db_conn):
    """Moving into a slot that only has a cancelled row frees it via DELETE then UPDATE."""
    occupied = book_appointment(
        db_conn,
        name="Temp",
        phone="+1T",
        date="2026-05-15",
        time="12:00",
    )
    mover = book_appointment(
        db_conn,
        name="Mover",
        phone="+1M",
        date="2026-05-15",
        time="13:00",
    )
    cancel_appointment(db_conn, occupied.id, phone="+1T")
    updated = modify_appointment_timeslot(
        db_conn,
        mover.id,
        phone="+1M",
        new_date="2026-05-15",
        new_time="12:00",
    )
    assert updated.date == "2026-05-15"
    assert updated.time == "12:00"


def test_unique_constraint_enforced_at_sql_level(db_conn):
    book_appointment(
        db_conn,
        name="X",
        phone="+1X",
        date="2026-05-16",
        time="14:00",
    )
    with pytest.raises(DoubleBookingError):
        book_appointment(
            db_conn,
            name="Y",
            phone="+1Y",
            date="2026-05-16",
            time="14:00",
        )
