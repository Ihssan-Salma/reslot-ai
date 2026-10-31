import hashlib
import json
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.context import TenantContext
from app.integrations.google.config import SHEETS_SCOPE
from app.integrations.google.errors import GoogleError
from app.integrations.google.http import GoogleHTTP
from app.models.automation import IdempotencyRecord, SpreadsheetConfiguration
from app.repositories.automation import AutomationRepository
from app.schemas.automation import (
    CustomerRecord,
    SpreadsheetStatus,
    WaitingListRecord,
    WaitlistStatus,
    stable_id,
)
from app.services.google import GoogleService

SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets/"
CUSTOMER_COLUMNS = ["customer_id", "full_name", "email", "phone", "notification_consent"]
WAITLIST_COLUMNS = [
    "entry_id",
    "customer_id",
    "requested_offering",
    "preferred_start",
    "preferred_end",
    "party_size",
    "quantity",
    "additional_preferences",
    "status",
]


class GoogleSheetsService:
    """Read external records; persist configuration and operation receipts only."""

    def __init__(self, db: Session, context: TenantContext, http: GoogleHTTP):
        self.db = db
        self.google = GoogleService(db, context, http)
        self.repository = AutomationRepository(db, context.tenant.id)

    def status(self) -> SpreadsheetStatus:
        config = self.repository.spreadsheet()
        connection = self.google.repository.connection()
        return SpreadsheetStatus(
            configured=config is not None,
            spreadsheet_id=config.spreadsheet_id if config else None,
            requires_reconsent=bool(connection and SHEETS_SCOPE not in connection.granted_scopes),
        )

    def authorize(self) -> None:
        connection = self.google.connected()
        if SHEETS_SCOPE not in connection.granted_scopes:
            raise GoogleError(
                "sheets_reconsent_required", "Reconnect Google to grant Sheets access", 409
            )
        if self.google.access_token is None:
            self.google.refresh(connection)
        if SHEETS_SCOPE not in connection.granted_scopes:
            raise GoogleError(
                "sheets_reconsent_required", "Reconnect Google to grant Sheets access", 409
            )

    def configured_id(self) -> str:
        self.authorize()
        config = self.repository.spreadsheet()
        connection = self.google.connected()
        if not config:
            raise GoogleError("spreadsheet_not_configured", "Configure a spreadsheet first", 409)
        if (
            config.google_subject != connection.google_subject
            or config.google_connection_id != connection.id
        ):
            raise GoogleError(
                "spreadsheet_account_changed",
                "Configure the spreadsheet for this Google account",
                409,
            )
        return config.spreadsheet_id

    def configure(self, spreadsheet_id: str) -> SpreadsheetStatus:
        self.authorize()
        metadata = self.google.get(
            SHEETS_URL + quote(spreadsheet_id, safe=""),
            {"fields": "spreadsheetId,sheets(properties(title))"},
        )
        titles = {item.get("properties", {}).get("title") for item in metadata.get("sheets", [])}
        if not {"Customers", "WaitingList"}.issubset(titles):
            raise GoogleError(
                "invalid_spreadsheet_schema",
                "Customers and WaitingList worksheets are required",
                422,
            )
        self.read_dataset(spreadsheet_id)
        connection = self.google.connected()
        config = self.repository.spreadsheet()
        if config is None:
            config = SpreadsheetConfiguration(tenant_id=self.google.tenant.id)
            self.db.add(config)
        config.google_connection_id = connection.id
        config.google_subject = connection.google_subject
        config.spreadsheet_id = spreadsheet_id
        self.db.flush()
        return self.status()

    def clear(self) -> dict[str, bool]:
        config = self.repository.spreadsheet()
        if config:
            self.db.delete(config)
        return {"configured": False}

    def read_rows(
        self, spreadsheet_id: str, sheet: str, columns: list[str]
    ) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
        # Include a look-ahead column and row to reject oversized or extended schemas.
        last_column = chr(ord("A") + len(columns))
        cell_range = quote(f"'{sheet}'!A1:{last_column}10002", safe="")
        payload = self.google.get(
            SHEETS_URL + quote(spreadsheet_id, safe="") + "/values/" + cell_range,
            {"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
        )
        values = payload.get("values", [])
        if not isinstance(values, list) or not values or not isinstance(values[0], list):
            raise GoogleError("invalid_spreadsheet_schema", f"{sheet} requires a header row", 422)
        headers = values[0]
        if (
            len(headers) != len(columns)
            or any(not isinstance(h, str) for h in headers)
            or set(headers) != set(columns)
        ):
            raise GoogleError(
                "invalid_spreadsheet_schema",
                f"{sheet} column names must match the documented schema",
                422,
            )
        if len(values) > 10001:
            raise GoogleError(
                "spreadsheet_too_large", "Worksheets are limited to 10000 data rows", 413
            )
        rows = []
        for number, row in enumerate(values[1:], start=2):
            if not isinstance(row, list) or len(row) > len(headers):
                raise GoogleError(
                    "invalid_spreadsheet_schema", f"{sheet} has unexpected cells", 422
                )
            if not any(cell not in (None, "") for cell in row):
                continue
            padded = row + [""] * (len(headers) - len(row))
            rows.append((number, dict(zip(headers, padded, strict=True))))
        return headers, rows

    def read_dataset(
        self, spreadsheet_id: str
    ) -> tuple[list[CustomerRecord], list[tuple[int, WaitingListRecord]], list[str]]:
        _, customers_raw = self.read_rows(spreadsheet_id, "Customers", CUSTOMER_COLUMNS)
        headers, waiting_raw = self.read_rows(spreadsheet_id, "WaitingList", WAITLIST_COLUMNS)
        try:
            customers = [CustomerRecord.model_validate(row) for _, row in customers_raw]
            waiting = [
                (number, WaitingListRecord.model_validate(row)) for number, row in waiting_raw
            ]
        except ValidationError:
            # Never return source cells, email addresses, or validation input in API errors.
            raise GoogleError(
                "invalid_spreadsheet_data",
                "Spreadsheet records fail the documented validation rules",
                422,
            ) from None
        customer_ids = {row.customer_id for row in customers}
        entry_ids = {row.entry_id for _, row in waiting}
        if len(customer_ids) != len(customers) or len(entry_ids) != len(waiting):
            raise GoogleError(
                "duplicate_spreadsheet_id", "Customer and entry IDs must be unique", 422
            )
        if any(row.customer_id not in customer_ids for _, row in waiting):
            raise GoogleError(
                "unknown_customer_reference", "WaitingList references an unknown customer ID", 422
            )
        return customers, waiting, headers

    def health(self) -> dict[str, Any]:
        customers, waiting, _ = self.read_dataset(self.configured_id())
        return {
            "healthy": True,
            "customer_count": len(customers),
            "waiting_list_count": len(waiting),
        }

    def get_customers(self) -> list[CustomerRecord]:
        return self.read_dataset(self.configured_id())[0]

    def get_customer(self, customer_id: str) -> CustomerRecord:
        self.validate_id(customer_id)
        row = next((row for row in self.get_customers() if row.customer_id == customer_id), None)
        if row is None:
            raise GoogleError(
                "customer_not_found", "Customer ID not found in this business spreadsheet", 404
            )
        return row

    def get_waiting_list(self) -> list[WaitingListRecord]:
        return [row for _, row in self.read_dataset(self.configured_id())[1]]

    def get_waitlist_entry(self, entry_id: str) -> WaitingListRecord:
        self.validate_id(entry_id)
        row = next((row for row in self.get_waiting_list() if row.entry_id == entry_id), None)
        if row is None:
            raise GoogleError(
                "waitlist_entry_not_found", "Entry ID not found in this business spreadsheet", 404
            )
        return row

    @staticmethod
    def validate_id(value: str) -> None:
        try:
            stable_id(value)
        except ValueError:
            raise GoogleError("invalid_record_id", "Invalid stable record ID", 422) from None

    def update_waitlist_status(
        self, entry_id: str, status: WaitlistStatus, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        self.validate_id(entry_id)
        # Validate direct service calls as well as API requests.
        from app.schemas.automation import WAITLIST_STATUSES

        if status not in WAITLIST_STATUSES:
            raise GoogleError("invalid_waitlist_status", "Unsupported waiting list status", 422)
        spreadsheet_id = self.configured_id()
        fingerprint = hashlib.sha256(
            json.dumps(
                [spreadsheet_id, self.google.connected().google_subject, entry_id, status]
            ).encode()
        ).hexdigest()
        key = idempotency_key or uuid4().hex
        receipt = self.repository.operation(key)
        if receipt and receipt.request_hash != fingerprint:
            raise GoogleError(
                "idempotency_conflict", "This operation key was used for a different request", 409
            )
        if receipt and receipt.status == "COMPLETED":
            return receipt.result or {}
        if receipt and receipt.status == "REVIEW":
            raise GoogleError(
                "sheet_changed_requires_review",
                "Review the spreadsheet before issuing a new operation",
                409,
            )
        if receipt is None:
            receipt = IdempotencyRecord(
                tenant_id=self.google.tenant.id,
                operation_key=key,
                request_hash=fingerprint,
                status="IN_PROGRESS",
            )
            self.db.add(receipt)
        try:
            # Resolve IDs on every attempt, immediately before writing. Never cache row numbers.
            _, entries, headers = self.read_dataset(spreadsheet_id)
            match = next(
                ((number, row) for number, row in entries if row.entry_id == entry_id), None
            )
            if match is None:
                raise GoogleError("waitlist_entry_not_found", "Entry ID not found", 404)
            number, row = match
            if row.status != status:
                column = chr(ord("A") + headers.index("status"))
                target = quote(f"'WaitingList'!{column}{number}", safe="")
                response = self.google.http.request(
                    "PUT",
                    SHEETS_URL + quote(spreadsheet_id, safe="") + "/values/" + target,
                    params={"valueInputOption": "RAW"},
                    headers={"Authorization": "Bearer " + (self.google.access_token or "")},
                    json={
                        "range": f"'WaitingList'!{column}{number}",
                        "majorDimension": "ROWS",
                        "values": [[status]],
                    },
                )
                if response.status_code == 401:
                    self.google.invalidate(self.google.connected())
                    raise GoogleError(
                        "google_reconnect_required", "Reconnect Google before retrying", 409
                    )
                if response.status_code in (403, 404):
                    raise GoogleError(
                        "google_permission_denied",
                        "Spreadsheet is not writable by the connected account",
                        403,
                    )
                if response.status_code != 200:
                    raise GoogleError(
                        "google_unavailable",
                        "Status update could not be confirmed. Retry with the same operation key.",
                        503,
                    )
                self.google.http.json(response)
            _, current_entries, _ = self.read_dataset(spreadsheet_id)
            verified = next(
                (
                    (position, item)
                    for position, item in current_entries
                    if item.entry_id == entry_id
                ),
                None,
            )
            if verified is None or verified[0] != number or verified[1].status != status:
                receipt.status = "REVIEW"
                raise GoogleError(
                    "sheet_changed_requires_review",
                    "Spreadsheet changed during the update; review it before retrying",
                    409,
                )
            receipt.status = "COMPLETED"
            receipt.result = {"entry_id": entry_id, "status": status, "idempotency_key": key}
            self.db.flush()
            return receipt.result
        except GoogleError:
            if receipt.status != "REVIEW":
                receipt.status = "FAILED"
            self.db.flush()
            raise
