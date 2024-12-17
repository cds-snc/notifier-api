""" locust-notifications.py
    isort:skip_file
"""
# flake8: noqa

import os
import sys
from datetime import datetime
from dataclasses import make_dataclass

sys.path.append(os.path.abspath(os.path.join("..", "tests_smoke")))

from dotenv import load_dotenv
from locust import HttpUser, constant_pacing, task
from tests_smoke.smoke.common import job_line, rows_to_csv  # type: ignore

load_dotenv()
NotifyApiUserTemplateGroup = make_dataclass(
    "NotifyApiUserTemplateGroup",
    [
        "bulk_email_id",
        "email_id",
        "email_with_attachment_id",
        "email_with_link_id",
        "sms_id",
    ],
)


class NotifyApiUser(HttpUser):
    wait_time = constant_pacing(60)
    host = os.getenv("PERF_TEST_DOMAIN", "https://api.staging.notification.cdssandbox.xyz")

    def __init__(self, *args, **kwargs):
        super(NotifyApiUser, self).__init__(*args, **kwargs)

        self.headers = {"Authorization": os.getenv("PERF_TEST_AUTH_HEADER")}
        self.email = os.getenv("PERF_TEST_EMAIL", "success@simulator.amazonses.com")
        self.phone_number = os.getenv("PERF_TEST_PHONE_NUMBER", "16132532222")
        self.template_group = NotifyApiUserTemplateGroup(
            bulk_email_id=os.getenv("PERF_TEST_BULK_EMAIL_TEMPLATE_ID"),
            email_id=os.getenv("PERF_TEST_EMAIL_TEMPLATE_ID"),
            email_with_attachment_id=os.getenv("PERF_TEST_EMAIL_WITH_ATTACHMENT_TEMPLATE_ID"),
            email_with_link_id=os.getenv("PERF_TEST_EMAIL_WITH_LINK_TEMPLATE_ID"),
            sms_id=os.getenv("PERF_TEST_SMS_TEMPLATE_ID"),
        )

    @task(16)
    def send_email_notifications(self):
        json = self.__email_json(self.template_group.email_id)

        self.client.post("/v2/notifications/email", json=json, headers=self.headers)

    @task(2)
    def send_email_with_attachment_notifications(self):
        personalisation = {
            "attached_file": {
                "file": "Q29udGVudCBvZiBBdHRhY2hlZCBmaWxl",
                "filename": "attached_file.txt",
                "sending_method": "attach",
            }
        }
        json = self.__email_json(self.template_group.email_with_attachment_id, personalisation)

        self.client.post("/v2/notifications/email", json=json, headers=self.headers)

    @task(2)
    def send_email_with_link_notifications(self):
        personalisation = {
            "application_file": {
                "file": "Q29udGVudCBvZiBBdHRhY2hlZCBmaWxl",
                "filename": "attached_file.txt",
                "sending_method": "link",
            }
        }
        json = self.__email_json(self.template_group.email_with_link_id, personalisation)

        self.client.post("/v2/notifications/email", json=json, headers=self.headers)

    @task(8)
    def send_bulk_notifications(self):
        json = {
            "name": f"My bulk name {datetime.utcnow().isoformat()}",
            "template_id": self.template_group.bulk_email_id,
            "csv": rows_to_csv([["email address", "application_file"], *job_line(self.email, 2)]),
        }

        self.client.post("/v2/notifications/bulk", json=json, headers=self.headers)

    @task(16)
    def send_sms_notifications(self):
        json = {"phone_number": self.phone_number, "template_id": self.template_group.sms_id}

        self.client.post("/v2/notifications/sms", json=json, headers=self.headers)

    def __email_json(self, template_id, personalisation={}):
        return {
            "email_address": self.email,
            "template_id": template_id,
            "personalisation": personalisation,
        }
