from datetime import datetime

import pytest
from freezegun import freeze_time
from tests.app.conftest import create_sample_notification
from tests.app.db import (
    create_notification,
    create_service_callback_api,
    save_notification,
)
from tests.conftest import set_config

from app import annual_limit_client, statsd_client
from app.aws.mocks import (
    pinpoint_delivered_callback,
    pinpoint_delivered_callback_missing_sms_data,
    pinpoint_failed_callback,
    pinpoint_shortcode_delivered_callback,
    pinpoint_successful_callback,
)
from app.celery.process_pinpoint_receipts_tasks import process_pinpoint_results
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


@pytest.mark.parametrize(
    "callback, expected_response, origination_phone_number",
    [
        (pinpoint_delivered_callback, "Message has been accepted by phone", "+13655550100"),
        (pinpoint_shortcode_delivered_callback, "Message has been accepted by phone carrier", "555555"),
    ],
)
def test_process_pinpoint_results_delivered(
    sample_template, notify_db, notify_db_session, callback, expected_response, origination_phone_number, mocker
):
    mock_info_logger = mocker.patch("app.celery.process_pinpoint_receipts_tasks.current_app.logger.info")
    mock_callback_task = mocker.patch("app.celery.process_pinpoint_receipts_tasks._check_and_queue_callback_task")
    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_template,
        reference="ref",
        status=NOTIFICATION_SENT,
        sent_by="pinpoint",
        sent_at=datetime.utcnow(),
    )
    assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT

    process_pinpoint_results(callback(reference="ref"))

    updated_notification = get_notification_by_id(notification.id)
    mock_callback_task.assert_called_once_with(updated_notification)
    assert updated_notification.status == NOTIFICATION_DELIVERED
    assert updated_notification.provider_response == expected_response
    assert float(updated_notification.sms_total_message_price) == 0.00581
    assert float(updated_notification.sms_total_carrier_fee) == 0.006
    assert updated_notification.sms_iso_country_code == "CA"
    assert updated_notification.sms_carrier_name == "Bell"
    assert updated_notification.sms_message_encoding == "GSM"
    assert updated_notification.sms_origination_phone_number == origination_phone_number
    mock_info_logger.assert_called_once_with(f"Pinpoint callback return status of delivered for notification: {notification.id}")


def test_process_pinpoint_results_succeeded(sample_template, notify_db, notify_db_session, mocker):
    mock_callback_task = mocker.patch("app.celery.process_pinpoint_receipts_tasks._check_and_queue_callback_task")

    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_template,
        reference="ref",
        status=NOTIFICATION_SENT,
        sent_by="pinpoint",
        sent_at=datetime.utcnow(),
    )
    assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT

    process_pinpoint_results(pinpoint_successful_callback(reference="ref"))

    updated_notification = get_notification_by_id(notification.id)
    mock_callback_task.assert_not_called()
    assert updated_notification.status == NOTIFICATION_SENT
    assert updated_notification.provider_response is None


def test_process_pinpoint_results_missing_sms_data(sample_template, notify_db, notify_db_session, mocker):
    mock_callback_task = mocker.patch("app.celery.process_pinpoint_receipts_tasks._check_and_queue_callback_task")

    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_template,
        reference="ref",
        status=NOTIFICATION_SENT,
        sent_by="pinpoint",
        sent_at=datetime.utcnow(),
    )
    assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT

    process_pinpoint_results(pinpoint_delivered_callback_missing_sms_data(reference="ref"))

    updated_notification = get_notification_by_id(notification.id)
    mock_callback_task.assert_called_once_with(updated_notification)
    assert updated_notification.status == NOTIFICATION_DELIVERED
    assert float(updated_notification.sms_total_message_price) == 0.00581
    assert float(updated_notification.sms_total_carrier_fee) == 0.006
    assert updated_notification.sms_iso_country_code is None
    assert updated_notification.sms_carrier_name is None


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
def test_process_pinpoint_results_failed(
    sample_template,
    notify_db,
    notify_db_session,
    mocker,
    provider_response,
    expected_status,
    should_log_warning,
    should_save_provider_response,
):
    mock_logger = mocker.patch("app.celery.process_pinpoint_receipts_tasks.current_app.logger.info")
    mock_warning_logger = mocker.patch("app.celery.process_pinpoint_receipts_tasks.current_app.logger.warning")
    mock_callback_task = mocker.patch("app.celery.process_pinpoint_receipts_tasks._check_and_queue_callback_task")

    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_template,
        reference="ref",
        status=NOTIFICATION_SENT,
        sent_by="pinpoint",
        sent_at=datetime.utcnow(),
    )
    assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT

    process_pinpoint_results(pinpoint_failed_callback(provider_response=provider_response, reference="ref"))

    updated_notification = get_notification_by_id(notification.id)
    mock_callback_task.assert_called_once_with(updated_notification)
    assert updated_notification.status == expected_status

    if should_save_provider_response:
        assert updated_notification.provider_response == provider_response
    else:
        assert updated_notification.provider_response is None

    mock_logger.assert_called_once_with(
        (
            f"Pinpoint delivery failed: notification id {notification.id} and reference ref has error found. "
            f"Provider response: {provider_response}"
        )
    )

    assert mock_warning_logger.call_count == int(should_log_warning)


def test_pinpoint_callback_should_retry_if_notification_is_missing(notify_db, mocker):
    mock_retry = mocker.patch("app.celery.process_pinpoint_receipts_tasks.process_pinpoint_results.retry")
    mock_callback_task = mocker.patch("app.celery.process_pinpoint_receipts_tasks._check_and_queue_callback_task")

    process_pinpoint_results(pinpoint_delivered_callback(reference="ref"))

    mock_callback_task.assert_not_called()
    assert mock_retry.call_count == 1


def test_pinpoint_callback_should_give_up_after_max_tries(notify_db, mocker):
    mocker.patch(
        "app.celery.process_pinpoint_receipts_tasks.process_pinpoint_results.retry",
        side_effect=MaxRetriesExceededError,
    )
    mock_warning_logger = mocker.patch("app.celery.process_pinpoint_receipts_tasks.current_app.logger.warning")
    mock_callback_task = mocker.patch("app.celery.process_pinpoint_receipts_tasks._check_and_queue_callback_task")

    process_pinpoint_results(pinpoint_delivered_callback(reference="ref")) is None
    mock_callback_task.assert_not_called()

    mock_warning_logger.assert_called_with("notification not found for Pinpoint reference: ref (update to delivered). Giving up.")


def test_process_pinpoint_results_retry_called(sample_template, mocker):
    save_notification(
        create_notification(
            sample_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status=NOTIFICATION_SENT,
            sent_by="pinpoint",
        )
    )
    mocker.patch(
        "app.dao.notifications_dao._update_notification_status",
        side_effect=Exception("EXPECTED"),
    )
    mock_retry = mocker.patch("app.celery.process_pinpoint_receipts_tasks.process_pinpoint_results.retry")

    process_pinpoint_results(response=pinpoint_delivered_callback(reference="ref1"))

    assert mock_retry.call_count == 1


def test_process_pinpoint_results_does_not_process_other_providers(sample_template, mocker):
    mock_exception_logger = mocker.patch("app.celery.process_pinpoint_receipts_tasks.current_app.logger.exception")
    mock_dao = mocker.patch("app.dao.notifications_dao._update_notification_status")
    save_notification(
        create_notification(
            sample_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status=NOTIFICATION_SENT,
            sent_by="sns",
        )
    )

    process_pinpoint_results(response=pinpoint_delivered_callback(reference="ref1")) is None

    mock_exception_logger.assert_called_once()
    mock_dao.assert_not_called()


def test_process_pinpoint_results_calls_service_callback(sample_template, notify_db_session, notify_db, mocker):
    with freeze_time("2021-01-01T12:00:00"):
        mocker.patch("app.statsd_client.incr")
        mocker.patch("app.statsd_client.timing_with_dates")
        mock_send_status = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")

        notification = create_sample_notification(
            notify_db,
            notify_db_session,
            template=sample_template,
            reference="ref",
            status=NOTIFICATION_SENT,
            sent_by="pinpoint",
            sent_at=datetime.utcnow(),
        )
        callback_api = create_service_callback_api(service=sample_template.service, url="https://example.com")
        assert get_notification_by_id(notification.id).status == NOTIFICATION_SENT

        process_pinpoint_results(pinpoint_delivered_callback(reference="ref"))

        updated_notification = get_notification_by_id(notification.id)
        assert updated_notification.status == NOTIFICATION_DELIVERED
        assert updated_notification.provider_response == "Message has been accepted by phone"
        statsd_client.timing_with_dates.assert_any_call("callback.pinpoint.elapsed-time", datetime.utcnow(), notification.sent_at)
        statsd_client.incr.assert_any_call("callback.pinpoint.delivered")
        signed_data = create_delivery_status_callback_data(updated_notification, callback_api)
        mock_send_status.assert_called_once_with(
            [str(notification.id), signed_data, notification.service_id], queue="service-callbacks"
        )


class TestAnnualLimits:
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
    def test_process_pinpoint_results_should_increment_sms_failed_when_delivery_receipt_is_failure(
        self,
        sample_sms_template_with_html,
        notify_api,
        mocker,
        provider_response,
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
                sent_by="pinpoint",
            )
        )
        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            process_pinpoint_results(pinpoint_failed_callback(reference="ref", provider_response=provider_response))
            annual_limit_client.increment_sms_failed.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_sms_delivered.assert_not_called()

    @pytest.mark.parametrize(
        "callback",
        [
            (pinpoint_delivered_callback),
            (pinpoint_shortcode_delivered_callback),
        ],
    )
    def test_process_pinpoint_results_should_increment_sms_delivered_when_delivery_receipt_is_success(
        self,
        sample_sms_template_with_html,
        notify_api,
        mocker,
        callback,
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
                sent_by="pinpoint",
            )
        )
        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            process_pinpoint_results(callback(reference="ref"))
            annual_limit_client.increment_sms_delivered.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_sms_failed.assert_not_called()

    @pytest.mark.parametrize(
        "callback, provider_response",
        [
            (pinpoint_delivered_callback, None),
            (pinpoint_failed_callback, "Blocked as spam by phone carrier"),
            (pinpoint_failed_callback, "Phone carrier is currently unreachable/unavailable"),
            (pinpoint_failed_callback, "Phone is currently unreachable/unavailable"),
            (pinpoint_failed_callback, "This is not a real response"),
        ],
    )
    def test_process_pinpoint_results_seeds_annual_limit_notifications_when_not_seeded_today_and_doesnt_increment_when_seeding(
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
                sent_by="pinpoint",
            )
        )
        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            process_pinpoint_results(
                callback(provider_response, reference="ref") if provider_response else callback(reference="ref")
            )
            annual_limit_client.set_seeded_at.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_sms_delivered.assert_not_called()
            annual_limit_client.increment_sms_failed.assert_not_called()
