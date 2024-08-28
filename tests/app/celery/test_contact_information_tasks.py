import pytest
import uuid
from app.celery.common import RETRIES_EXCEEDED
from app.celery.contact_information_tasks import lookup_contact_info
from app.celery.exceptions import AutoRetryException
from app.exceptions import NotificationTechnicalFailureException, NotificationPermanentFailureException
from app.models import (
    EMAIL_TYPE,
    Notification,
    NOTIFICATION_TECHNICAL_FAILURE,
    NOTIFICATION_PERMANENT_FAILURE,
    RecipientIdentifier,
    SMS_TYPE,
)
from app.va.identifier import IdentifierType
from app.va.va_profile import (
    NoContactInfoException,
    VAProfileClient,
    VAProfileNonRetryableException,
    VAProfileRetryableException,
)
from requests import Timeout


EXAMPLE_VA_PROFILE_ID = '135'
notification_id = str(uuid.uuid4())


def test_should_get_email_address_and_update_notification(client, mocker, sample_template, sample_notification):
    template = sample_template(template_type=EMAIL_TYPE)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )

    mocked_get_notification_by_id = mocker.patch(
        'app.celery.contact_information_tasks.get_notification_by_id', return_value=notification
    )

    mocked_va_profile_client = mocker.Mock(VAProfileClient)
    mocked_va_profile_client.get_email = mocker.Mock(return_value='test@test.org')
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)

    mocked_update_notification = mocker.patch('app.celery.contact_information_tasks.dao_update_notification')

    lookup_contact_info(notification.id)

    mocked_get_notification_by_id.assert_called()
    mocked_va_profile_client.get_email.assert_called_with(mocker.ANY)
    recipient_identifier = mocked_va_profile_client.get_email.call_args[0][0]
    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID
    mocked_update_notification.assert_called_with(notification)
    assert notification.to == 'test@test.org'


def test_should_get_phone_number_and_update_notification(client, mocker, sample_notification):
    notification = sample_notification(
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}]
    )
    assert notification.notification_type == SMS_TYPE
    mocked_get_notification_by_id = mocker.patch(
        'app.celery.contact_information_tasks.get_notification_by_id', return_value=notification
    )

    mocked_va_profile_client = mocker.Mock(VAProfileClient)
    mocked_va_profile_client.get_telephone = mocker.Mock(return_value='+15555555555')
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)

    mocked_update_notification = mocker.patch('app.celery.contact_information_tasks.dao_update_notification')

    lookup_contact_info(notification.id)

    mocked_get_notification_by_id.assert_called()
    mocked_va_profile_client.get_telephone.assert_called_with(mocker.ANY)
    recipient_identifier = mocked_va_profile_client.get_telephone.call_args[0][0]
    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID
    mocked_update_notification.assert_called_with(notification)
    assert notification.to == '+15555555555'


def test_should_not_retry_on_non_retryable_exception(client, mocker, sample_template, sample_notification):
    template = sample_template(template_type=EMAIL_TYPE)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )

    mocker.patch('app.celery.contact_information_tasks.get_notification_by_id', return_value=notification)

    mocked_check_and_queue_callback_task = mocker.patch(
        'app.celery.contact_information_tasks.check_and_queue_callback_task',
    )

    mocked_va_profile_client = mocker.Mock(VAProfileClient)

    exception = VAProfileNonRetryableException
    mocked_va_profile_client.get_email = mocker.Mock(side_effect=exception)
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)

    mocked_update_notification_status_by_id = mocker.patch(
        'app.celery.contact_information_tasks.update_notification_status_by_id'
    )

    with pytest.raises(NotificationPermanentFailureException) as exc_info:
        lookup_contact_info(notification.id)

    mocked_va_profile_client.get_email.assert_called_with(mocker.ANY)
    recipient_identifier = mocked_va_profile_client.get_email.call_args[0][0]
    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID

    mocked_update_notification_status_by_id.assert_called_with(
        notification.id, NOTIFICATION_PERMANENT_FAILURE, status_reason=exception.failure_reason
    )
    mocked_check_and_queue_callback_task.assert_called_once_with(notification)


@pytest.mark.parametrize('exception_type', (Timeout, VAProfileRetryableException))
def test_should_retry_on_retryable_exception(client, mocker, sample_template, sample_notification, exception_type):
    template = sample_template(template_type=EMAIL_TYPE)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )
    mocker.patch('app.celery.contact_information_tasks.get_notification_by_id', return_value=notification)

    mocked_va_profile_client = mocker.Mock(VAProfileClient)
    mocked_va_profile_client.get_email = mocker.Mock(side_effect=exception_type('some error'))
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)

    with pytest.raises(AutoRetryException) as exc_info:
        lookup_contact_info(notification.id)

    mocked_va_profile_client.get_email.assert_called_with(mocker.ANY)
    recipient_identifier = mocked_va_profile_client.get_email.call_args[0][0]
    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID


@pytest.mark.parametrize('notification_type', (SMS_TYPE, EMAIL_TYPE))
def test_lookup_contact_info_should_retry_on_timeout(
    client, mocker, sample_template, sample_notification, notification_type
):
    template = sample_template(template_type=notification_type)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )

    mocker.patch('app.celery.contact_information_tasks.get_notification_by_id', return_value=notification)

    mocked_va_profile_client = mocker.Mock(VAProfileClient)

    if notification_type == SMS_TYPE:
        mocked_va_profile_client.get_telephone = mocker.Mock(side_effect=Timeout('Request timed out'))
    else:
        mocked_va_profile_client.get_email = mocker.Mock(side_effect=Timeout('Request timed out'))

    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)

    with pytest.raises(AutoRetryException) as exc_info:
        lookup_contact_info(notification.id)

    assert exc_info.value.args[0] == 'Found Timeout, autoretrying...'
    assert isinstance(exc_info.value.args[1], Timeout)
    assert str(exc_info.value.args[1]) == 'Request timed out'

    if notification_type == SMS_TYPE:
        mocked_va_profile_client.get_telephone.assert_called_with(mocker.ANY)
        recipient_identifier = mocked_va_profile_client.get_telephone.call_args[0][0]
    else:
        mocked_va_profile_client.get_email.assert_called_with(mocker.ANY)
        recipient_identifier = mocked_va_profile_client.get_email.call_args[0][0]

    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID


def test_should_update_notification_to_technical_failure_on_max_retries(
    client, mocker, sample_template, sample_notification
):
    template = sample_template(template_type=EMAIL_TYPE)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )
    mocker.patch('app.celery.contact_information_tasks.get_notification_by_id', return_value=notification)

    mocked_va_profile_client = mocker.Mock(VAProfileClient)
    mocked_va_profile_client.get_email = mocker.Mock(side_effect=VAProfileRetryableException)
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)
    mocker.patch('app.celery.contact_information_tasks.can_retry', return_value=False)
    mocked_handle_max_retries_exceeded = mocker.patch(
        'app.celery.contact_information_tasks.handle_max_retries_exceeded'
    )

    with pytest.raises(NotificationTechnicalFailureException) as exc_info:
        lookup_contact_info(notification.id)

    mocked_va_profile_client.get_email.assert_called_with(mocker.ANY)
    recipient_identifier = mocked_va_profile_client.get_email.call_args[0][0]
    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID

    mocked_handle_max_retries_exceeded.assert_called_once()


def test_should_update_notification_to_permanent_failure_on_no_contact_info_exception(
    client, mocker, sample_template, sample_notification
):
    template = sample_template(template_type=EMAIL_TYPE)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )
    mocker.patch('app.celery.contact_information_tasks.get_notification_by_id', return_value=notification)

    mocked_va_profile_client = mocker.Mock(VAProfileClient)
    exception = NoContactInfoException
    mocked_va_profile_client.get_email = mocker.Mock(side_effect=exception)
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)

    mocked_check_and_queue_callback_task = mocker.patch(
        'app.celery.contact_information_tasks.check_and_queue_callback_task',
    )

    mocked_update_notification_status_by_id = mocker.patch(
        'app.celery.contact_information_tasks.update_notification_status_by_id'
    )

    with pytest.raises(NotificationPermanentFailureException) as exc_info:
        lookup_contact_info(notification.id)

    mocked_va_profile_client.get_email.assert_called_with(mocker.ANY)
    recipient_identifier = mocked_va_profile_client.get_email.call_args[0][0]
    assert isinstance(recipient_identifier, RecipientIdentifier)
    assert recipient_identifier.id_value == EXAMPLE_VA_PROFILE_ID

    mocked_update_notification_status_by_id.assert_called_with(
        notification.id, NOTIFICATION_PERMANENT_FAILURE, status_reason=exception.failure_reason
    )

    mocked_check_and_queue_callback_task.assert_called_once_with(notification)


@pytest.mark.parametrize(
    'exception, throws_additional_exception, notification_status, exception_reason',
    [
        (
            VAProfileRetryableException,
            NotificationTechnicalFailureException,
            NOTIFICATION_TECHNICAL_FAILURE,
            RETRIES_EXCEEDED,
        ),
        (
            NoContactInfoException,
            NotificationPermanentFailureException,
            NOTIFICATION_PERMANENT_FAILURE,
            NoContactInfoException.failure_reason,
        ),
        (
            VAProfileNonRetryableException,
            NotificationPermanentFailureException,
            NOTIFICATION_PERMANENT_FAILURE,
            VAProfileNonRetryableException.failure_reason,
        ),
    ],
)
def test_exception_sets_failure_reason_if_thrown(
    client,
    mocker,
    sample_template,
    sample_notification,
    exception,
    throws_additional_exception,
    notification_status,
    exception_reason,
):
    template = sample_template(template_type=EMAIL_TYPE)
    notification = sample_notification(
        template=template,
        recipient_identifiers=[{'id_type': IdentifierType.VA_PROFILE_ID.value, 'id_value': EXAMPLE_VA_PROFILE_ID}],
    )
    mocker.patch('app.celery.contact_information_tasks.get_notification_by_id', return_value=notification)

    mocked_va_profile_client = mocker.Mock(VAProfileClient)
    mocked_va_profile_client.get_email = mocker.Mock(side_effect=exception)
    mocker.patch('app.celery.contact_information_tasks.va_profile_client', new=mocked_va_profile_client)
    mocker.patch('app.celery.contact_information_tasks.can_retry', return_value=False)

    mocked_check_and_queue_callback_task = mocker.patch(
        'app.celery.contact_information_tasks.check_and_queue_callback_task',
    )

    if exception_reason == RETRIES_EXCEEDED:
        mocker_handle_max_retries_exceeded = mocker.patch(
            'app.celery.contact_information_tasks.handle_max_retries_exceeded'
        )
        with pytest.raises(throws_additional_exception):
            lookup_contact_info(notification.id)
        mocker_handle_max_retries_exceeded.assert_called_once()
    else:
        mocked_update_notification_status_by_id = mocker.patch(
            'app.celery.contact_information_tasks.update_notification_status_by_id'
        )
        if throws_additional_exception:
            with pytest.raises(throws_additional_exception):
                lookup_contact_info(notification.id)
        else:
            lookup_contact_info(notification.id)
            mocked_update_notification_status_by_id.assert_called_once_with(
                notification.id, notification_status, status_reason=exception_reason
            )

    mocked_check_and_queue_callback_task.assert_called_once_with(notification)