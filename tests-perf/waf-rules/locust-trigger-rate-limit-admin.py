"""locust-trigger-rate-limit-admin.py

Trigger rate limit on our WAF rules for the admin website
on the following endpoints:

* Sign-in
* Register
* forgot password
* forced password reset

Once the necessary rate limit has been attained, the
tests will start to fail as expected.
"""
# flake8: noqa

from locust import HttpUser, constant_pacing, task


class NotifyAdminUser(HttpUser):
    host = "https://notification.canada.ca"
    spawn_rate = 10
    wait_time = constant_pacing(1)

    def __init__(self, *args, **kwargs):
        super(NotifyAdminUser, self).__init__(*args, **kwargs)
        self.headers = {}

    @task(1)
    def trigger_signin_block(self):
        self.client.get("/sign-in", headers=self.headers)

    @task(1)
    def trigger_register_block(self):
        self.client.get("/register", headers=self.headers)

    @task(1)
    def trigger_forgot_pw_block(self):
        self.client.get("/forgot-password", headers=self.headers)

    @task(1)
    def trigger_forced_pw_reset_block(self):
        self.client.get("/forced-password-reset", headers=self.headers)
