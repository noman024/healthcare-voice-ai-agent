"""SQLite persistence for appointments."""

from app.db.appointments import (
    Appointment,
    AppointmentConflictError,
    AppointmentNotFoundError,
    DoubleBookingError,
    book_appointment,
    cancel_appointment,
    get_appointment_by_id,
    list_appointments_for_phone,
    modify_appointment_timeslot,
)
from app.db.database import connect, get_db_path, init_db

__all__ = [
    "Appointment",
    "AppointmentConflictError",
    "AppointmentNotFoundError",
    "DoubleBookingError",
    "book_appointment",
    "cancel_appointment",
    "connect",
    "get_appointment_by_id",
    "get_db_path",
    "init_db",
    "list_appointments_for_phone",
    "modify_appointment_timeslot",
]
