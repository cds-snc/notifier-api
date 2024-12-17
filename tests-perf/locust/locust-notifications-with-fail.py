""" locust-notifications-with-bounce.py
    isort:skip_file
"""
# flake8: noqa

import os
import random
from dataclasses import make_dataclass

from dotenv import load_dotenv
from locust import HttpUser, constant_pacing, task

load_dotenv()
NotifyApiUserTemplateGroup = make_dataclass(
    "NotifyApiUserTemplateGroup",
    [
        "email_id",
        "email_with_attachment_id",
        "email_with_link_id",
    ],
)


class NotifyApiUser(HttpUser):
    wait_time = constant_pacing(60)
    host = os.getenv("PERF_TEST_DOMAIN", "https://api.staging.notification.cdssandbox.xyz")

    def __init__(self, *args, **kwargs):
        super(NotifyApiUser, self).__init__(*args, **kwargs)

        self.headers = {"Authorization": os.getenv("PERF_TEST_AUTH_HEADER")}
        self.fail_rate = float(os.getenv("PERF_TEST_FAIL_RATE", "0.1"))
        self.email_success = os.getenv("PERF_TEST_EMAIL_SUCCESS", "success@simulator.amazonses.com")
        self.template_group = NotifyApiUserTemplateGroup(
            email_id=os.getenv("PERF_TEST_EMAIL_TEMPLATE_ID"),
            email_with_attachment_id=os.getenv("PERF_TEST_EMAIL_WITH_ATTACHMENT_TEMPLATE_ID"),
            email_with_link_id=os.getenv("PERF_TEST_EMAIL_WITH_LINK_TEMPLATE_ID"),
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

    def __email_json(self, template_id, personalisation={}):
        email_invalid = [
            "complaint@simulator.amazonses.com",
            "bounce@simulator.amazonses.com",
            "ooto@simulator.amazonses.com",
            "blacklist@simulator.amazonses.com",
        ]
        email_index = random.randint(0, len(email_invalid) - 1)
        email = email_invalid[email_index] if random.random() <= self.fail_rate else self.email_success
        return {
            "email_address": email,
            "template_id": template_id,
            "personalisation": personalisation,
        }
