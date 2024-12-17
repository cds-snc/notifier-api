"""locust-trigger-rate-limit-documentation.py

Trigger rate limit on our WAF rules for the documentation website
on the following endpoints:

* /

Once the necessary rate limit has been attained, the
tests will start to fail as expected.
"""
# flake8: noqa

from locust import HttpUser, constant_pacing, task


class NotifyDocumentationUser(HttpUser):
    host = "https://documentation.notification.canada.ca/"
    spawn_rate = 10
    wait_time = constant_pacing(1)

    def __init__(self, *args, **kwargs):
        super(NotifyDocumentationUser, self).__init__(*args, **kwargs)
        self.headers = {}

    @task(1)
    def trigger_home_block(self):
        self.client.get("/", headers=self.headers)
