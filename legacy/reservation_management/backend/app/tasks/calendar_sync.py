import logging

import httpx

from app.core.celery_app import celery_app
from app.db.session import assert_runtime_role, get_engine
from app.integrations.google.http import GoogleHTTP
from app.services.calendar_sync import claim_work, process_claim

logger = logging.getLogger(__name__)


@celery_app.task(name="reslot.drain_calendar_outbox")  # type: ignore[untyped-decorator]
def drain_calendar_outbox() -> int:
    engine = get_engine()
    assert_runtime_role(engine)
    claims = claim_work(engine)
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for claim in claims:
            try:
                process_claim(engine, claim, GoogleHTTP(client))
            except Exception:
                # A crash/commit failure retains its lease; the dispatcher recovers expired work.
                logger.error("Calendar synchronization interrupted; its lease will be retried")
    return len(claims)
