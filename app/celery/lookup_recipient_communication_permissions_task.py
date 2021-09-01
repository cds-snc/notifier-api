from flask import current_app
from notifications_utils.statsd_decorators import statsd

from app import notify_celery, va_profile_client
from app.config import QueueNames
from app.dao.communication_item_dao import get_communication_item
from app.dao.notifications_dao import update_notification_status_by_id
from app.dao.templates_dao import dao_get_template_by_id
from app.exceptions import NotificationTechnicalFailureException
from app.feature_flags import FeatureFlag, is_feature_enabled
from app.models import RecipientIdentifier, NOTIFICATION_PREFERENCES_DECLINED, NOTIFICATION_TECHNICAL_FAILURE
from app.va.va_profile import VAProfileRetryableException
from app.va.va_profile.va_profile_client import CommunicationItemNotFoundException


@notify_celery.task(
    bind=True, name="lookup-recipient-communication-permissions", max_retries=5, default_retry_delay=300
)
@statsd(namespace="tasks")
def lookup_recipient_communication_permissions(
        self, id_type: str, id_value: str, template_id: str, notification_id: str
) -> None:
    current_app.logger.info(f"Looking up contact information for notification_id:{notification_id}.")

    if not recipient_has_given_permission(self, id_type, id_value, template_id, notification_id):
        update_notification_status_by_id(notification_id, NOTIFICATION_PREFERENCES_DECLINED)
        current_app.logger.info(f"Recipient for notification {notification_id}"
                                f"has declined permission to receive notifications")
        self.request.chain = None


def recipient_has_given_permission(task, id_type: str, id_value: str, template_id: str, notification_id: str) -> bool:
    if not is_feature_enabled(FeatureFlag.CHECK_USER_COMMUNICATION_PERMISSIONS_ENABLED):
        current_app.logger.info(f'Communication item permissions feature flag is off')
        return True

    identifier = RecipientIdentifier(id_type=id_type, id_value=id_value)
    template = dao_get_template_by_id(template_id)

    communication_item_id = template.communication_item_id

    if not communication_item_id:
        current_app.logger.info(
            f'User {id_value} does not have requested communication item id for notification {notification_id}'
        )
        return True

    communication_item = get_communication_item(communication_item_id)

    try:
        is_allowed = va_profile_client.get_is_communication_allowed(
            identifier, communication_item.va_profile_item_id, notification_id
        )
        current_app.logger.info(f'Value of permission for item {communication_item.va_profile_item_id} for user '
                                f'{id_value} for notification {notification_id}: {is_allowed}')
        return is_allowed
    except VAProfileRetryableException as e:
        current_app.logger.exception(e)
        try:
            task.retry(queue=QueueNames.RETRY)
        except task.MaxRetriesExceededError:
            message = (
                'RETRY FAILED: Max retries reached. '
                f'The task lookup_contact_info failed for notification {notification_id}. '
                'Notification has been updated to technical-failure'
            )

            update_notification_status_by_id(
                notification_id, NOTIFICATION_TECHNICAL_FAILURE, status_reason=e.failure_reason
            )
            raise NotificationTechnicalFailureException(message) from e
    except CommunicationItemNotFoundException:
        current_app.logger.info(f'Communication item for user {id_value} not found on notification {notification_id}')
        return True
