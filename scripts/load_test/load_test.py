import csv
import os
from datetime import datetime
from io import StringIO
from typing import Iterator, List

from dotenv import load_dotenv
from locust import HttpUser, constant_pacing, task

load_dotenv()


def rows_to_csv(rows: List[List[str]]):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


def job_lines(data: str, number_of_lines: int) -> Iterator[List[str]]:
    return map(lambda n: [data], range(0, number_of_lines))


class NotifyApiUser(HttpUser):
    wait_time = constant_pacing(1)  # do something every second

    def __init__(self, *args, **kwargs):
        super(NotifyApiUser, self).__init__(*args, **kwargs)

        self.headers = {"Authorization": f"apikey-v1 {os.getenv('API_KEY')}"}
        self.email_address = "success@simulator.amazonses.com"
        self.phone_number = "16135550123"  # INTERNAL_TEST_NUMBER, does not actually send SMS
        self.high_priority_email_template = os.getenv("HIGH_PRIORITY_EMAIL_TEMPLATE_ID")
        self.low_priority_email_template = os.getenv("LOW_PRIORITY_EMAIL_TEMPLATE_ID")
        self.high_priority_sms_template = os.getenv("HIGH_PRIORITY_SMS_TEMPLATE_ID")
        self.low_priority_sms_template = os.getenv("LOW_PRIORITY_SMS_TEMPLATE_ID")

    def send_bulk_email(self, count: int):
        json = {
            "name": f"Low priority emails {datetime.utcnow().isoformat()}",
            "template_id": self.low_priority_email_template,
            "csv": rows_to_csv([["email address"], *job_lines(self.email_address, count)])
        }
        self.client.post("/v2/notifications/bulk", json=json, headers=self.headers, timeout=60)

    def send_bulk_sms(self, count: int):
        json = {
            "name": f"Low priority sms {datetime.utcnow().isoformat()}",
            "template_id": self.low_priority_sms_template,
            "csv": rows_to_csv([["phone_number"], *job_lines(self.phone_number, 1000)])
        }
        self.client.post("/v2/notifications/bulk", json=json, headers=self.headers, timeout=60)

    @task(297)  # about every 2 seconds
    def send_high_priority_email(self):
        json = {"email_address": self.email_address, "template_id": self.high_priority_email_template}
        self.client.post("/v2/notifications/email", json=json, headers=self.headers)

    @task(298)  # about every 2 seconds
    def send_priority_sms(self):
        json = {"phone_number": self.phone_number, "template_id": self.high_priority_sms_template}
        self.client.post("/v2/notifications/sms", json=json, headers=self.headers)

    @task(2)  # about every 5 minutes
    def send_low_priority_emails_1(self):
        self.send_bulk_email(5000)

    @task(1)  # about every 10 minutes
    def send_low_priority_emails_2(self):
        self.send_bulk_email(5000)

    @task(2)  # about every 5 minutes
    def send_low_priority_sms(self):
        self.send_bulk_sms(1000)