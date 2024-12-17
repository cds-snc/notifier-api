from dotenv import load_dotenv
from locust import HttpUser, TaskSet, constant_pacing, task
from locust.clients import HttpSession
from soak_utils import url_with_prefix

load_dotenv()


class MultipleHostsUser(HttpUser):
    abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.admin_client = HttpSession(base_url=self.host, request_event=self.client.request_event, user=self)

        self.api_client = HttpSession(
            base_url=url_with_prefix(self.host, "api"), request_event=self.client.request_event, user=self
        )

        self.api_k8s_client = HttpSession(
            base_url=url_with_prefix(self.host, "api-k8s"), request_event=self.client.request_event, user=self
        )

        self.dd_api_client = HttpSession(
            base_url=url_with_prefix(self.host, "api.document"), request_event=self.client.request_event, user=self
        )

        self.documentation_client = HttpSession(
            base_url=url_with_prefix(self.host, "documentation"), request_event=self.client.request_event, user=self
        )


class UserTasks(TaskSet):
    @task
    def test_admin(self):
        self.user.admin_client.get("/_status?simple=true", name=f"{self.user.admin_client.base_url}/_status?simple=true")

    @task
    def test_api(self):
        self.user.api_client.get("/_status?status=true", name=f"{self.user.api_client.base_url}/_status?simple=true")

    @task
    def test_api_k8s(self):
        self.user.api_k8s_client.get("/_status?status=true", name=f"{self.user.api_k8s_client.base_url}/_status?simple=true")

    @task
    def test_dd_api(self):
        self.user.dd_api_client.get("/_status?simple=true", name=f"{self.user.dd_api_client.base_url}/_status?simple=true")

    @task
    def test_documentation(self):
        self.user.documentation_client.get("/", name=f"{self.user.documentation_client.base_url}/")


class WebsiteUser(MultipleHostsUser):
    wait_time = constant_pacing(0.2)  # 5 GETs a second, so each server every second on average
    tasks = [UserTasks]
