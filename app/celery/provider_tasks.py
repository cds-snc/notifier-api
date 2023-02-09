from flask import current_app
from notifications_utils.recipients import InvalidEmailError
from notifications_utils.statsd_decorators import statsd
from sqlalchemy.orm.exc import NoResultFound

from app import notify_celery
from app.config import QueueNames
from app.dao import notifications_dao
from app.dao.notifications_dao import update_notification_status_by_id
from app.delivery import send_to_providers
from app.exceptions import InvalidUrlException, NotificationTechnicalFailureException
from app.models import NOTIFICATION_TECHNICAL_FAILURE
from app.notifications.callbacks import _check_and_queue_callback_task


# Celery rate limits are per worker instance and not a global rate limit.
# https://docs.celeryproject.org/en/stable/userguide/tasks.html#Task.rate_limit
# This task is dispatched through the `send-throttled-sms-tasks` queue.
# This queue is consumed by 1 Celery instance with 1 worker, the SMS Celery pod.
# The maximum throughput is therefore 1 instance * 1 worker = 1 task per rate limit.
# We the set rate_limit="30/m" on the Celery task to have 1 task per 2 seconds.
@notify_celery.task(
    bind=True,
    name="deliver_throttled_sms",
    max_retries=48,
    default_retry_delay=300,
    rate_limit="30/m",
)
@statsd(namespace="tasks")
def deliver_throttled_sms(self, notification_id):
    _deliver_sms(self, notification_id)


# Celery rate limits are per worker instance and not a global rate limit.
# https://docs.celeryproject.org/en/stable/userguide/tasks.html#Task.rate_limit
# This task is dispatched through the `send-sms-tasks` queue.
# This queue is consumed by 6 Celery instances with 4 workers in production.
# The maximum throughput is therefore 6 instances * 4 workers = 24 tasks per second
# if we set rate_limit="1/s" on the Celery task
@notify_celery.task(
    bind=True,
    name="deliver_sms",
    max_retries=48,
    default_retry_delay=300,
    rate_limit="1/s",
)
@statsd(namespace="tasks")
def deliver_sms(self, notification_id):
    _deliver_sms(self, notification_id)


@notify_celery.task(bind=True, name="deliver_email", max_retries=48, default_retry_delay=300)
@statsd(namespace="tasks")
def deliver_email(self, notification_id):
    try:
        current_app.logger.info("Start sending email for notification id: {}".format(notification_id))
        notification = notifications_dao.get_notification_by_id(notification_id)
        if not notification:
            raise NoResultFound()
        send_to_providers.send_email_to_provider(notification)
    except InvalidEmailError as e:
        if not notification.to.isascii():
            current_app.logger.info(f"Cannot send notification {notification_id} (has a non-ascii email address): {str(e)}")
        else:
            current_app.logger.info(f"Cannot send notification {notification_id}, got an invalid email address: {str(e)}.")
        update_notification_status_by_id(notification_id, NOTIFICATION_TECHNICAL_FAILURE)
        _check_and_queue_callback_task(notification)
    except InvalidUrlException:
        current_app.logger.error(f"Cannot send notification {notification_id}, got an invalid direct file url.")
        update_notification_status_by_id(notification_id, NOTIFICATION_TECHNICAL_FAILURE)
        _check_and_queue_callback_task(notification)
    except Exception as e:
        try:
            current_app.logger.warning(f"The exception is {repr(e)}")
            if self.request.retries <= 10:
                current_app.logger.warning("RETRY {}: Email notification {} failed".format(self.request.retries, notification_id))
            else:
                current_app.logger.exception("RETRY: Email notification {} failed".format(notification_id))
            self.retry(queue=QueueNames.RETRY)
        except self.MaxRetriesExceededError:
            message = (
                "RETRY FAILED: Max retries reached. "
                "The task send_email_to_provider failed for notification {}. "
                "Notification has been updated to technical-failure".format(notification_id)
            )
            update_notification_status_by_id(notification_id, NOTIFICATION_TECHNICAL_FAILURE)
            _check_and_queue_callback_task(notification)
            raise NotificationTechnicalFailureException(message)


def _deliver_sms(self, notification_id):
    try:
        current_app.logger.info("Start sending SMS for notification id: {}".format(notification_id))
        notification = notifications_dao.get_notification_by_id(notification_id)
        if not notification:
            raise NoResultFound()
        send_to_providers.send_sms_to_provider(notification)
    except InvalidUrlException:
        current_app.logger.error(f"Cannot send notification {notification_id}, got an invalid direct file url.")
        update_notification_status_by_id(notification_id, NOTIFICATION_TECHNICAL_FAILURE)
        _check_and_queue_callback_task(notification)
    except Exception:
        try:
            if self.request.retries == 0:
                # Retry immediately, especially as a common failure is for the database data
                # replication to be delayed. The immediate retry likely succeeds in these scenarios.
                self.retry(queue=QueueNames.RETRY, countdown=0)
            else:
                # Once the previous retry failed, log the exception and this time,
                # retry with the default delay.
                current_app.logger.exception("SMS notification delivery for id: {} failed".format(notification_id))
                self.retry(queue=QueueNames.RETRY)
        except self.MaxRetriesExceededError:
            message = (
                "RETRY FAILED: Max retries reached. The task send_sms_to_provider failed for notification {}. "
                "Notification has been updated to technical-failure".format(notification_id)
            )
            update_notification_status_by_id(notification_id, NOTIFICATION_TECHNICAL_FAILURE)
            _check_and_queue_callback_task(notification)
            raise NotificationTechnicalFailureException(message)
