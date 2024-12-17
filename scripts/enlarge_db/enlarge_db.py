import argparse
import sys
from datetime import datetime
from typing import List

from flask import Flask

sys.path.append("../..")
from app import create_app, create_uuid, db  # noqa: E402
from app.config import Config  # noqa: E402
from app.models import NotificationHistory  # noqa: E402

DEFAULT_CHUNK_SIZE = 10000


def create_notifications(n: int, ref: str) -> List[NotificationHistory]:
    notifications = [
        NotificationHistory(
            id=create_uuid(),
            created_at=datetime.utcnow(),
            template_id=Config.NEW_USER_EMAIL_VERIFICATION_TEMPLATE_ID,
            template_version=1,
            service_id=Config.NOTIFY_SERVICE_ID,
            notification_type="email",
            key_type="normal",
            client_reference=ref,
        )
        for _ in range(n)
    ]
    return notifications


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-n",
        "--notifications",
        default=1,
        type=int,
        help="number of notifications to add to the notification_history table (default 1)",
    )
    parser.add_argument(
        "-r",
        "--reference",
        default="manually created",
        type=str,
        help="client reference to use for the notifications (default 'manually created')",
    )
    parser.add_argument(
        "-c",
        "--chunksize",
        default=DEFAULT_CHUNK_SIZE,
        type=int,
        help=f"chunk size for bulk_save_objects (default {DEFAULT_CHUNK_SIZE})",
    )
    args = parser.parse_args()

    app = Flask("enlarge_db")
    create_app(app)

    for notifications_done in range(0, args.notifications, args.chunksize):
        notifications = create_notifications(min(args.chunksize, args.notifications - notifications_done), args.reference)
        print(f"Adding {len(notifications)} notifications to notification_history")
        with app.app_context():
            try:
                db.session.bulk_save_objects(notifications)
                db.session.commit()
            except Exception as e:
                print(f"Error adding notifications: {e}")
                db.session.rollback()
                sys.exit(1)
        print(f"Done {notifications_done+len(notifications)} / {args.notifications}")
