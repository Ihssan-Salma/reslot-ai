"""Real Google smoke test through the authenticated API; read-only unless explicitly requested."""

import argparse
from getpass import getpass
from uuid import uuid4

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5173")
    parser.add_argument("--email", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--event-id", required=True, help="An existing marked Google Calendar event"
    )
    parser.add_argument("--entry-id", help="Required only for an explicit status-write smoke test")
    parser.add_argument(
        "--write-status",
        choices=["WAITING", "OFFERED", "ACCEPTED", "DECLINED", "CANCELLED", "EXPIRED", "INACTIVE"],
    )
    args = parser.parse_args()
    if args.write_status and not args.entry_id:
        parser.error("--write-status requires --entry-id; use a synthetic test entry")
    if not args.base_url.startswith(("http://localhost:", "http://127.0.0.1:", "https://")):
        parser.error("Use a localhost or HTTPS API origin")

    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=60, follow_redirects=False
    ) as client:

        def request(method: str, path: str, body: object = None) -> object:
            headers = {"Origin": args.base_url.rstrip("/")}
            if method != "GET":
                csrf = client.get("/api/v1/auth/csrf")
                csrf.raise_for_status()
                headers["X-CSRF-Token"] = csrf.json()["csrf_token"]
            response = client.request(method, "/api/v1" + path, json=body, headers=headers)
            if response.status_code >= 400:
                # Do not print provider data, cookies, passwords, or source records.
                raise RuntimeError(
                    f"{method} integration request failed: HTTP {response.status_code}"
                )
            return response.json()

        request(
            "POST", "/auth/login", {"email": args.email, "password": getpass("ReSlot password: ")}
        )
        try:
            request("POST", "/tenants/switch", {"tenant_id": args.tenant_id})
            connection = request("GET", "/integrations/google")
            assert isinstance(connection, dict) and connection["connection_status"] == "connected"
            request("GET", "/integrations/google/gmail")
            health = request("GET", "/integrations/sheets/health")
            assert isinstance(health, dict) and health["healthy"] is True
            customers = request("GET", "/integrations/sheets/customers")
            entries = request("GET", "/integrations/sheets/waiting-list")
            assert isinstance(customers, list) and isinstance(entries, list)
            if customers:
                request("GET", "/integrations/sheets/customers/" + customers[0]["customer_id"])
            if entries:
                request("GET", "/integrations/sheets/waiting-list/" + entries[0]["entry_id"])
            event = request("GET", "/integrations/calendar/reservations/" + args.event_id)
            assert isinstance(event, dict) and event["classification"] == "reservation", (
                "Event needs valid reservation metadata"
            )
            reservation = event["reservation"]
            request(
                "POST",
                "/integrations/calendar/availability",
                {"start_at": reservation["start_at"], "end_at": reservation["end_at"]},
            )
            if args.write_status:
                original = request("GET", "/integrations/sheets/waiting-list/" + args.entry_id)
                assert isinstance(original, dict)
                path = "/integrations/sheets/waiting-list/" + args.entry_id + "/status"
                request("POST", path, {"status": args.write_status, "idempotency_key": uuid4().hex})
                # Restore after a confirmed write; failures require manual review.
                request(
                    "POST", path, {"status": original["status"], "idempotency_key": uuid4().hex}
                )
            print(
                "PASS: tenant, Gmail, Sheets schema/read/lookup, Calendar metadata and availability"
            )
            if args.write_status:
                print("PASS: explicit test status update and restoration")
        finally:
            request("POST", "/auth/logout", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
