from typing import Any

from app.models import Offering, Reservation, Tenant


def calendar_body(reservation: Reservation, offering: Offering, tenant: Tenant) -> dict[str, Any]:
    if reservation.local_start_date and reservation.local_end_date:
        start = {"date": reservation.local_start_date.isoformat()}
        end = {"date": reservation.local_end_date.isoformat()}
    else:
        start = {"dateTime": reservation.start_at.isoformat(), "timeZone": tenant.timezone}
        end = {"dateTime": reservation.end_at.isoformat(), "timeZone": tenant.timezone}
    return {
        "summary": offering.name,
        "description": "Managed by ReSlot AI. Reservation " + str(reservation.id) + ".",
        "start": start,
        "end": end,
        "extendedProperties": {
            "private": {
                "reslot_tenant": str(tenant.id),
                "reslot_reservation": str(reservation.id),
                "reslot_version": str(reservation.sync_version),
            }
        },
    }
