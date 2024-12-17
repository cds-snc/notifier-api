import uuid
from datetime import datetime

import pytest
from freezegun import freeze_time
from tests.app.conftest import create_sample_notification
from tests.app.db import (
    create_notification,
    create_service,
    create_service_callback_api,
    create_template,
    create_user,
    save_notification,
)
from tests.conftest import set_config

from app import annual_limit_client, statsd_client
from app.aws.mocks import sns_failed_callback, sns_success_callback
from app.celery.process_sns_receipts_tasks import process_sns_results
from app.celery.reporting_tasks import create_nightly_notification_status_for_day
from app.dao.notifications_dao import get_notification_by_id
from app.models import (
    NOTIFICATION_DELIVERED,
    NOTIFICATION_PERMANENT_FAILURE,
    NOTIFICATION_SENT,
    NOTIFICATION_TECHNICAL_FAILURE,
    NOTIFICATION_TEMPORARY_FAILURE,
)
from app.notifications.callbacks import create_delivery_status_callback_data
from celery.exceptions import MaxRetriesExceededError


def test_process_sns_results_delivered(sample_template, notify_db, notify_db_session, mocker):
    mock_logger = mocker.patch("app.celery.process_sns_receipts_tasks.current_app.logger.info")

    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_template,
        reference="ref",
        status=NOTIFICATION_SENT,
        sent_by="sns",
        sent_at=datetime.utcnow(),
    )
    assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT
    assert process_sns_results(sns_success_callback(reference="ref"))
    assert get_notification_by_id(notification.id).status == NOTIFICATION_DELIVERED
    assert get_notification_by_id(notification.id).provider_response == "Message has been accepted by phone carrier"

    mock_logger.assert_called_once_with(f"SNS callback return status of delivered for notification: {notification.id}")


@pytest.mark.parametrize(
    "provider_response, expected_status, should_log_warning, should_save_provider_response",
    [
        (
            "Blocked as spam by phone carrier",
            NOTIFICATION_TECHNICAL_FAILURE,
            False,
            True,
        ),
        (
            "Phone carrier is currently unreachable/unavailable",
            NOTIFICATION_TEMPORARY_FAILURE,
            False,
            True,
        ),
        (
            "Phone is currently unreachable/unavailable",
            NOTIFICATION_PERMANENT_FAILURE,
            False,
            True,
        ),
        ("This is not a real response", NOTIFICATION_TECHNICAL_FAILURE, True, True),
    ],
)
def test_process_sns_results_failed(
    sample_template,
    notify_db,
    notify_db_session,
    mocker,
    provider_response,
    expected_status,
    should_log_warning,
    should_save_provider_response,
):
    mock_logger = mocker.patch("app.celery.process_sns_receipts_tasks.current_app.logger.info")
    mock_warning_logger = mocker.patch("app.celery.process_sns_receipts_tasks.current_app.logger.warning")

    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_template,
        reference="ref",
        status=NOTIFICATION_SENT,
        sent_by="sns",
        sent_at=datetime.utcnow(),
    )
    assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT
    assert process_sns_results(sns_failed_callback(provider_response=provider_response, reference="ref"))
    assert get_notification_by_id(notification.id).status == expected_status

    if should_save_provider_response:
        assert get_notification_by_id(notification.id).provider_response == provider_response
    else:
        assert get_notification_by_id(notification.id).provider_response is None

    mock_logger.assert_called_once_with(
        (
            f"SNS delivery failed: notification id {notification.id} and reference ref has error found. "
            f"Provider response: {provider_response}"
        )
    )

    assert mock_warning_logger.call_count == int(should_log_warning)


def test_sns_callback_should_retry_if_notification_is_missing(notify_db, mocker):
    mock_retry = mocker.patch("app.celery.process_sns_receipts_tasks.process_sns_results.retry")
    assert process_sns_results(sns_success_callback(reference="ref")) is None
    assert mock_retry.call_count == 1


def test_sns_callback_should_give_up_after_max_tries(notify_db, mocker):
    mocker.patch(
        "app.celery.process_sns_receipts_tasks.process_sns_results.retry",
        side_effect=MaxRetriesExceededError,
    )
    mock_logger = mocker.patch("app.celery.process_sns_receipts_tasks.current_app.logger.warning")

    assert process_sns_results(sns_success_callback(reference="ref")) is None
    mock_logger.assert_called_with("notification not found for SNS reference: ref (update to delivered). Giving up.")


def test_process_sns_results_retry_called(sample_template, mocker):
    save_notification(
        create_notification(
            sample_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status=NOTIFICATION_SENT,
            sent_by="sns",
        )
    )

    mocker.patch(
        "app.dao.notifications_dao._update_notification_status",
        side_effect=Exception("EXPECTED"),
    )
    mocked = mocker.patch("app.celery.process_sns_receipts_tasks.process_sns_results.retry")
    process_sns_results(response=sns_success_callback(reference="ref1"))
    assert mocked.call_count == 1


def test_process_sns_results_does_not_process_other_providers(sample_template, mocker):
    mock_logger = mocker.patch("app.celery.process_sns_receipts_tasks.current_app.logger.exception")
    mock_dao = mocker.patch("app.dao.notifications_dao._update_notification_status")
    save_notification(
        create_notification(
            sample_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status=NOTIFICATION_SENT,
            sent_by="pinpoint",
        )
    )

    process_sns_results(response=sns_success_callback(reference="ref1")) is None
    mock_logger.assert_called_once()
    mock_dao.assert_not_called()


def test_process_sns_results_calls_service_callback(sample_template, notify_db_session, notify_db, mocker):
    with freeze_time("2021-01-01T12:00:00"):
        mocker.patch("app.statsd_client.incr")
        mocker.patch("app.statsd_client.timing_with_dates")
        send_mock = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
        notification = create_sample_notification(
            notify_db,
            notify_db_session,
            template=sample_template,
            reference="ref",
            status=NOTIFICATION_SENT,
            sent_by="sns",
            sent_at=datetime.utcnow(),
        )
        callback_api = create_service_callback_api(service=sample_template.service, url="https://example.com")
        assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT

        assert process_sns_results(sns_success_callback(reference="ref"))
        assert get_notification_by_id(notification.id).status == NOTIFICATION_DELIVERED
        assert get_notification_by_id(notification.id).provider_response == "Message has been accepted by phone carrier"
        statsd_client.timing_with_dates.assert_any_call("callback.sns.elapsed-time", datetime.utcnow(), notification.sent_at)
        statsd_client.incr.assert_any_call("callback.sns.delivered")
        updated_notification = get_notification_by_id(notification.id)
        signed_data = create_delivery_status_callback_data(updated_notification, callback_api)
        send_mock.assert_called_once_with(
            [str(notification.id), signed_data, notification.service_id],
            queue="service-callbacks",
        )


class TestAnnualLimit:
    def test_sns_callback_should_increment_sms_delivered_when_delivery_receipt_is_delivered(
        self, sample_sms_template_with_html, notify_api, mocker
    ):
        mocker.patch("app.annual_limit_client.increment_sms_delivered")
        mocker.patch("app.annual_limit_client.increment_sms_failed")
        mocker.patch("app.annual_limit_client.was_seeded_today", return_value=True)

        notification = save_notification(
            create_notification(
                sample_sms_template_with_html,
                reference="ref",
                sent_at=datetime.utcnow(),
                status=NOTIFICATION_SENT,
                sent_by="sns",
            )
        )
        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            assert process_sns_results(sns_success_callback(reference="ref"))

            annual_limit_client.increment_sms_delivered.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_sms_failed.assert_not_called()

    @freeze_time("2019-04-01T5:30")
    def test_create_nightly_notification_status_for_day_clears_failed_delivered_notification_counts(
        self, sample_template, notify_api, mocker
    ):
        service_ids = []
        for i in range(39):
            user = create_user(email=f"test{i}@test.ca", mobile_number=f"{i}234567890")
            service = create_service(service_id=uuid.uuid4(), service_name=f"service{i}", user=user, email_from=f"best.email{i}")
            template_sms = create_template(service=service)
            template_email = create_template(service=service, template_type="email")

            save_notification(create_notification(template_sms, status="delivered", created_at=datetime(2019, 4, 1, 5, 0)))
            save_notification(create_notification(template_email, status="delivered", created_at=datetime(2019, 4, 1, 5, 0)))
            save_notification(create_notification(template_sms, status="failed", created_at=datetime(2019, 4, 1, 5, 0)))
            save_notification(create_notification(template_email, status="failed", created_at=datetime(2019, 4, 1, 5, 0)))

            mapping = {"sms_failed": 1, "sms_delivered": 1, "email_failed": 1, "email_delivered": 1}
            annual_limit_client.seed_annual_limit_notifications(service.id, mapping)
            service_ids.append(service.id)

        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            create_nightly_notification_status_for_day("2019-04-01")

        for service_id in service_ids:
            assert all(value == 0 for value in annual_limit_client.get_all_notification_counts(service_id).values())

    @pytest.mark.parametrize(
        "provider_response",
        [
            "Blocked as spam by phone carrier",
            "Destination is on a blocked list",
            "Invalid phone number",
            "Message body is invalid",
            "Phone carrier has blocked this message",
            "Phone carrier is currently unreachable/unavailable",
            "Phone has blocked SMS",
            "Phone is on a blocked list",
            "Phone is currently unreachable/unavailable",
            "Phone number is opted out",
            "This delivery would exceed max price",
            "Unknown error attempting to reach phone",
        ],
    )
    def test_sns_callback_should_increment_sms_failed_when_delivery_receipt_is_failure(
        self, sample_sms_template_with_html, notify_api, mocker, provider_response
    ):
        mocker.patch("app.annual_limit_client.increment_sms_delivered")
        mocker.patch("app.annual_limit_client.increment_sms_failed")
        mocker.patch("app.annual_limit_client.was_seeded_today", return_value=True)

        notification = save_notification(
            create_notification(
                sample_sms_template_with_html,
                reference="ref",
                sent_at=datetime.utcnow(),
                status=NOTIFICATION_SENT,
                sent_by="sns",
            )
        )

        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            assert process_sns_results(sns_failed_callback(reference="ref", provider_response=provider_response))
            annual_limit_client.increment_sms_failed.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_sms_delivered.assert_not_called()

    @pytest.mark.parametrize(
        "callback, provider_response",
        [
            (sns_success_callback, None),
            (sns_failed_callback, "Blocked as spam by phone carrier"),
            (sns_failed_callback, "Phone carrier is currently unreachable/unavailable"),
            (sns_failed_callback, "Phone is currently unreachable/unavailable"),
            (sns_failed_callback, "This is not a real response"),
        ],
    )
    def test_process_sns_results_seeds_annual_limit_notifications_when_not_seeded_today_and_doesnt_increment_when_seeding(
        self,
        callback,
        provider_response,
        sample_sms_template_with_html,
        notify_api,
        mocker,
    ):
        mocker.patch("app.annual_limit_client.increment_sms_delivered")
        mocker.patch("app.annual_limit_client.increment_sms_failed")
        mocker.patch("app.annual_limit_client.was_seeded_today", return_value=False)
        mocker.patch("app.annual_limit_client.set_seeded_at")

        notification = save_notification(
            create_notification(
                sample_sms_template_with_html,
                reference="ref",
                sent_at=datetime.utcnow(),
                status=NOTIFICATION_SENT,
                sent_by="sns",
            )
        )
        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            process_sns_results(callback(provider_response, reference="ref") if provider_response else callback(reference="ref"))
            annual_limit_client.set_seeded_at.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_sms_delivered.assert_not_called()
            annual_limit_client.increment_sms_failed.assert_not_called()
