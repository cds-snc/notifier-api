from datetime import datetime
from typing import Optional

from app.encryption import NotificationDictToSign
from app.models import Job, NotificationType, Service


class VerifiedNotification(NotificationDictToSign):
    service: Service
    recipient: str  # to
    notification_type: NotificationType
    created_at: datetime
    job_row_number: Optional[int]
