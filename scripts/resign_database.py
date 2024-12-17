"""
Script to resign certain database fields:
 - api key secrets
 - inbound sms content
 - service callback bearer_tokens

Needs Notify config variables and access to the database. In AWS run on an api pod.

Usage (run from the scripts/ folder):
    python resign_database.py [unsafe]
    - unsafe: unsign regardless of whether the current secret key can verify the signature
"""

import argparse
import sys

from dotenv import load_dotenv
from flask import Flask

sys.path.append("..")  # needed so we can find app (as run from scripts/ folder)

from flask import current_app  # noqa: E402

from app import create_app  # noqa: E402
from app.dao.api_key_dao import resign_api_keys  # noqa: E402
from app.dao.inbound_sms_dao import resign_inbound_sms  # noqa: E402
from app.dao.notifications_dao import resign_notifications  # noqa: E402
from app.dao.service_callback_api_dao import resign_service_callbacks  # noqa: E402


def resign_all(chunk: int, resign: bool, unsafe: bool, notifications: bool):
    resign_api_keys(resign, unsafe)
    resign_inbound_sms(resign, unsafe)
    resign_service_callbacks(resign, unsafe)
    if notifications:
        resign_notifications(chunk, resign, unsafe)
    if not resign:
        current_app.logger.info("NOTE: this is a preview, fields have not been changed. To resign fields, run with --resign flag")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--notifications", default=False, action="store_true", help="resign notifications (default false)")
    parser.add_argument(
        "-c", "--chunk", default=25000, type=int, help="size of chunks of notifications to resign at a time (default 25000)"
    )
    parser.add_argument("-r", "--resign", default=False, action="store_true", help="resign columns (default false)")
    parser.add_argument("-u", "--unsafe", default=False, action="store_true", help="ignore bad signatures (default false)")

    args = parser.parse_args()

    load_dotenv()
    application = Flask("resign_database")
    create_app(application)
    application.app_context().push()

    resign_all(args.chunk, args.resign, args.unsafe, args.notifications)
