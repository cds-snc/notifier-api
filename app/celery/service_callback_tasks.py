import json

from flask import current_app
from notifications_utils.statsd_decorators import statsd
from requests import HTTPError, RequestException, request

from app import notify_celery, signer_complaint, signer_delivery_status
from app.config import QueueNames
from app.models import Service

# Uncomment when we implement email sending for callback failures
# from requests.exceptions import InvalidURL, Timeout


@notify_celery.task(bind=True, name="send-delivery-status", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def send_delivery_status_to_service(self, notification_id, signed_status_update):
    status_update = signer_delivery_status.verify(signed_status_update)

    data = {
        "id": str(notification_id),
        "reference": status_update["notification_client_reference"],
        "to": status_update["notification_to"],
        "status": status_update["notification_status"],
        "status_description": status_update["notification_status_description"],
        "provider_response": status_update["notification_provider_response"],
        "created_at": status_update["notification_created_at"],
        "completed_at": status_update["notification_updated_at"],
        "sent_at": status_update["notification_sent_at"],
        "notification_type": status_update["notification_type"],
    }
    _send_data_to_service_callback_api(
        self,
        data,
        status_update["service_callback_api_url"],
        status_update["service_callback_api_bearer_token"],
        "send_delivery_status_to_service",
    )


@notify_celery.task(bind=True, name="send-complaint", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def send_complaint_to_service(self, complaint_data):
    complaint = signer_complaint.verify(complaint_data)

    data = {
        "notification_id": complaint["notification_id"],
        "complaint_id": complaint["complaint_id"],
        "reference": complaint["reference"],
        "to": complaint["to"],
        "complaint_date": complaint["complaint_date"],
    }

    _send_data_to_service_callback_api(
        self,
        data,
        complaint["service_callback_api_url"],
        complaint["service_callback_api_bearer_token"],
        "send_complaint_to_service",
    )


def _send_data_to_service_callback_api(self, data, service_callback_url, token, function_name):
    notification_id = data["notification_id"] if "notification_id" in data else data["id"]
    try:
        current_app.logger.info("{} sending {} to {}".format(function_name, notification_id, service_callback_url))
        response = request(
            method="POST",
            url=service_callback_url,
            data=json.dumps(data),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=5,
        )

        current_app.logger.info(
            f"{function_name} sending {notification_id} to {service_callback_url}, response {response.status_code}"
        )

        response.raise_for_status()
    except RequestException as e:
        current_app.logger.warning(
            f"{function_name} request failed for notification_id: {notification_id} and url: {service_callback_url}. exc: {e}"
        )

        # TODO: Instate once we monitor alarms to determine how often this happens and we implement
        #       check_cloudwatch_for_callback_failures(), otherwise we risk flooding the service
        #       owner's inbox with callback failure email notifications.

        # if isinstance(e, Timeout) or isinstance(e, InvalidURL) or e.response.status_code == 500:
        #     if check_cloudwatch_for_callback_failures():
        #         send_email_callback_failure_email(current_app.service)

        # Retry if the response status code is server-side or 429 (too many requests).
        if not isinstance(e, HTTPError) or e.response.status_code >= 500 or e.response.status_code == 429:
            try:
                self.retry(queue=QueueNames.CALLBACKS_RETRY)
            except self.MaxRetriesExceededError:
                current_app.logger.warning(
                    f"Retry: {function_name} has retried the max num of times for callback url {service_callback_url} and notification_id: {notification_id} for service: {current_app.service.id}"
                )


def send_email_callback_failure_email(service: Service):
    service.send_notification_to_service_users(
        service_id=service.id,
        template_id=current_app.config["CALLBACK_FAILURE_TEMPLATE_ID"],
        personalisation={
            "service_name": service.name,
            "contact_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
            "callback_doc_url": f"{current_app.config['DOCUMENTATION_DOAMIN']}/en/callbacks.html",
        },
        include_user_fields=["name"],
    )


def check_cloudwatch_for_callback_failures():
    """
    TODO: Use boto3 to check cloudwatch for callback failures
    https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs/client/start_query.html

    Check if a service has failed 5 callbacks in a 30 minute time period

    ----------------

    import boto3
    from datetime import datetime, timedelta
    import time

    client = boto3.client('logs')

    query = "TODO"

    log_group = 'TODO'

    start_query_response = client.start_query(
        logGroupName=log_group,
        startTime=int((datetime.today() - timedelta(minutes=30)).timestamp()),
        endTime=int(datetime.now().timestamp()),
        queryString=query,
    )

    query_id = start_query_response['queryId']

    response = None

    while response == None or response['status'] == 'Running':
        print('Waiting for query to complete ...')
        time.sleep(1)
        response = client.get_query_results(
            queryId=query_id
        )

    """
