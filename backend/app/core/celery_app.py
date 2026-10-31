from celery import Celery

from app.core.config import get_settings

# Infrastructure only. LangGraph/MCP workflows are not implemented in this task.
celery_app = Celery("reslot", broker=get_settings().redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], timezone="UTC", beat_schedule={}
)
