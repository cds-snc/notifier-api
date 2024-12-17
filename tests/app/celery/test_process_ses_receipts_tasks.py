import json
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

from app import annual_limit_client, bounce_rate_client, signer_complaint, statsd_client
from app.aws.mocks import ses_complaint_callback, ses_unknown_bounce_callback
from app.celery.process_ses_receipts_tasks import process_ses_results
from app.celery.research_mode_tasks import (
    ses_hard_bounce_callback,
    ses_notification_callback,
    ses_soft_bounce_callback,
)
from app.dao.notifications_dao import get_notification_by_id
from app.models import (
    NOTIFICATION_HARD_BOUNCE,
    NOTIFICATION_HARD_GENERAL,
    NOTIFICATION_HARD_NOEMAIL,
    NOTIFICATION_HARD_ONACCOUNTSUPPRESSIONLIST,
    NOTIFICATION_HARD_SUPPRESSED,
    NOTIFICATION_PERMANENT_FAILURE,
    NOTIFICATION_SOFT_ATTACHMENTREJECTED,
    NOTIFICATION_SOFT_BOUNCE,
    NOTIFICATION_SOFT_CONTENTREJECTED,
    NOTIFICATION_SOFT_GENERAL,
    NOTIFICATION_SOFT_MAILBOXFULL,
    NOTIFICATION_SOFT_MESSAGETOOLARGE,
    NOTIFICATION_UNKNOWN_BOUNCE,
    Complaint,
    Notification,
)
from app.notifications.callbacks import create_delivery_status_callback_data
from app.notifications.notifications_ses_callback import (
    remove_emails_from_bounce,
    remove_emails_from_complaint,
)
from celery.exceptions import MaxRetriesExceededError


def test_process_ses_results(sample_email_template):
    save_notification(
        create_notification(
            sample_email_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status="sending",
        )
    )

    assert process_ses_results(response=ses_notification_callback(reference="ref1"))


def test_process_ses_results_retry_called(sample_email_template, notify_db, mocker):
    save_notification(
        create_notification(
            sample_email_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status="sending",
        )
    )

    mocker.patch(
        "app.dao.notifications_dao._update_notification_status",
        side_effect=Exception("EXPECTED"),
    )
    mocked = mocker.patch("app.celery.process_ses_receipts_tasks.process_ses_results.retry")
    process_ses_results(response=ses_notification_callback(reference="ref1"))
    assert mocked.call_count != 0


def test_process_ses_results_in_complaint(sample_email_template, mocker):
    notification = save_notification(create_notification(template=sample_email_template, reference="ref1"))
    mocked = mocker.patch("app.dao.notifications_dao.update_notification_status_by_reference")
    process_ses_results(response=ses_complaint_callback())
    assert mocked.call_count == 0
    complaints = Complaint.query.all()
    assert len(complaints) == 1
    assert complaints[0].notification_id == notification.id


def test_remove_emails_from_complaint():
    test_json = json.loads(ses_complaint_callback()["Message"])
    remove_emails_from_complaint(test_json)
    assert "recipient1@example.com" not in json.dumps(test_json)


def test_remove_email_from_bounce():
    test_json = json.loads(ses_hard_bounce_callback(reference="ref1")["Message"])
    remove_emails_from_bounce(test_json)
    assert "bounce@simulator.amazonses.com" not in json.dumps(test_json)


def test_ses_callback_should_update_notification_status(notify_db, notify_db_session, sample_email_template, mocker):
    with freeze_time("2001-01-01T12:00:00"):
        mocker.patch("app.statsd_client.incr")
        mocker.patch("app.statsd_client.timing_with_dates")
        send_mock = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
        notification = create_sample_notification(
            notify_db,
            notify_db_session,
            template=sample_email_template,
            reference="ref",
            status="sending",
            sent_at=datetime.utcnow(),
        )
        callback_api = create_service_callback_api(service=sample_email_template.service, url="https://original_url.com")
        assert get_notification_by_id(notification.id).status == "sending"

        assert process_ses_results(ses_notification_callback(reference="ref"))
        notification = get_notification_by_id(notification.id)
        assert notification.status == "delivered"
        assert notification.provider_response is None
        statsd_client.timing_with_dates.assert_any_call("callback.ses.elapsed-time", datetime.utcnow(), notification.sent_at)
        statsd_client.incr.assert_any_call("callback.ses.delivered")
        updated_notification = Notification.query.get(notification.id)
        encrypted_data = create_delivery_status_callback_data(updated_notification, callback_api)
        send_mock.assert_called_once_with(
            [str(notification.id), encrypted_data, notification.service_id], queue="service-callbacks"
        )


def test_ses_callback_dont_change_hard_bounce_status(sample_template, mocker):
    with freeze_time("2001-01-01T12:00:00"):
        mocker.patch("app.statsd_client.incr")
        mocker.patch("app.statsd_client.timing_with_dates")
        mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
        notification = save_notification(
            create_notification(
                sample_template,
                status=NOTIFICATION_PERMANENT_FAILURE,
                reference="ref",
            )
        )
        notification = get_notification_by_id(notification.id)
        assert notification.status == NOTIFICATION_PERMANENT_FAILURE
        assert process_ses_results(ses_notification_callback(reference="ref"))
        notification = get_notification_by_id(notification.id)
        assert notification.status == NOTIFICATION_PERMANENT_FAILURE


def test_ses_callback_should_update_notification_status_when_receiving_new_delivery_receipt(sample_email_template, mocker):
    notification = save_notification(create_notification(template=sample_email_template, reference="ref", status="delivered"))

    assert process_ses_results(ses_hard_bounce_callback(reference="ref"))
    assert get_notification_by_id(notification.id).status == "permanent-failure"


def test_ses_callback_should_retry_if_notification_is_new(notify_db, mocker):
    mock_retry = mocker.patch("app.celery.process_ses_receipts_tasks.process_ses_results.retry")
    mock_logger = mocker.patch("app.celery.process_ses_receipts_tasks.current_app.logger.error")

    with freeze_time("2017-11-17T12:14:03.646Z"):
        assert process_ses_results(ses_notification_callback(reference="ref")) is None
        assert mock_logger.call_count == 0
        assert mock_retry.call_count == 1


def test_process_ses_receipts_tasks_exception_handling(notify_db, mocker):
    reference = "test_reference"
    mocker.patch("app.celery.process_ses_receipts_tasks.process_ses_results.retry", side_effect=MaxRetriesExceededError())
    mock_warning = mocker.patch("app.celery.process_ses_receipts_tasks.current_app.logger.error")

    with pytest.raises(Exception):
        process_ses_results(ses_notification_callback(reference))
        assert mock_warning.call_count == 2
        assert "RETRY 5: notification not found for SES reference test_reference." in mock_warning.call_args_list[0][0][0]
        assert (
            "notification not found for SES reference: test_reference. Error has persisted > number of retries. Giving up."
            in mock_warning.call_args_list[1][0][0]
        )


def test_ses_callback_should_retry_if_notification_is_missing(notify_db, mocker):
    mock_retry = mocker.patch("app.celery.process_ses_receipts_tasks.process_ses_results.retry")
    assert process_ses_results(ses_notification_callback(reference="ref")) is None
    assert mock_retry.call_count == 1


def test_ses_callback_should_give_up_after_max_tries(notify_db, mocker):
    mocker.patch(
        "app.celery.process_ses_receipts_tasks.process_ses_results.retry",
        side_effect=MaxRetriesExceededError,
    )
    mock_logger = mocker.patch("app.celery.process_ses_receipts_tasks.current_app.logger.warning")

    assert process_ses_results(ses_notification_callback(reference="ref")) is None
    mock_logger.assert_called_with("notification not found for SES reference: ref. Giving up.")


def test_ses_callback_does_not_call_send_delivery_status_if_no_db_entry(
    notify_db, notify_db_session, sample_email_template, mocker
):
    with freeze_time("2001-01-01T12:00:00"):
        send_mock = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
        notification = create_sample_notification(
            notify_db,
            notify_db_session,
            template=sample_email_template,
            reference="ref",
            status="sending",
            sent_at=datetime.utcnow(),
        )

        assert get_notification_by_id(notification.id).status == "sending"

        assert process_ses_results(ses_notification_callback(reference="ref"))
        notification = get_notification_by_id(notification.id)
        assert notification.status == "delivered"
        assert notification.provider_response is None

        send_mock.assert_not_called()


def test_ses_callback_should_update_multiple_notification_status_sent(
    notify_db, notify_db_session, sample_email_template, mocker
):
    send_mock = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
    create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_email_template,
        reference="ref1",
        sent_at=datetime.utcnow(),
        status="sending",
    )

    create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_email_template,
        reference="ref2",
        sent_at=datetime.utcnow(),
        status="sending",
    )

    create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_email_template,
        reference="ref3",
        sent_at=datetime.utcnow(),
        status="sending",
    )
    create_service_callback_api(service=sample_email_template.service, url="https://original_url.com")
    assert process_ses_results(ses_notification_callback(reference="ref1"))
    assert process_ses_results(ses_notification_callback(reference="ref2"))
    assert process_ses_results(ses_notification_callback(reference="ref3"))
    assert send_mock.called


@pytest.mark.parametrize(
    "bounce_subtype, provider_response",
    [
        ["General", None],
        ["AttachmentRejected", "The email was rejected because of its attachments"],
    ],
)
def test_ses_callback_should_set_status_to_temporary_failure(
    notify_db,
    notify_db_session,
    sample_email_template,
    mocker,
    bounce_subtype,
    provider_response,
):
    send_mock = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_email_template,
        reference="ref",
        status="sending",
        sent_at=datetime.utcnow(),
    )
    create_service_callback_api(service=notification.service, url="https://original_url.com")
    assert get_notification_by_id(notification.id).status == "sending"
    assert process_ses_results(ses_soft_bounce_callback(reference="ref", bounce_subtype=bounce_subtype))

    notification = get_notification_by_id(notification.id)
    assert notification.status == "temporary-failure"
    assert notification.provider_response == provider_response
    assert send_mock.called


@pytest.mark.parametrize(
    "bounce_subtype, provider_response",
    [
        ["General", None],
        ["Suppressed", "The email address is on our email provider suppression list"],
        [
            "OnAccountSuppressionList",
            "The email address is on the GC Notify suppression list",
        ],
    ],
)
def test_ses_callback_should_set_status_to_permanent_failure(
    notify_db, notify_db_session, sample_email_template, mocker, bounce_subtype, provider_response
):
    send_mock = mocker.patch("app.celery.service_callback_tasks.send_delivery_status_to_service.apply_async")
    notification = create_sample_notification(
        notify_db,
        notify_db_session,
        template=sample_email_template,
        reference="ref",
        status="sending",
        sent_at=datetime.utcnow(),
    )
    create_service_callback_api(service=sample_email_template.service, url="https://original_url.com")

    assert get_notification_by_id(notification.id).status == "sending"
    assert process_ses_results(ses_hard_bounce_callback(reference="ref", bounce_subtype=bounce_subtype))

    notification = get_notification_by_id(notification.id)
    assert notification.status == "permanent-failure"
    assert notification.provider_response == provider_response
    assert send_mock.called


def test_ses_callback_should_send_on_complaint_to_user_callback_api(sample_email_template, mocker):
    send_mock = mocker.patch("app.celery.service_callback_tasks.send_complaint_to_service.apply_async")
    create_service_callback_api(
        service=sample_email_template.service,
        url="https://original_url.com",
        callback_type="complaint",
    )

    notification = save_notification(
        create_notification(
            template=sample_email_template,
            reference="ref1",
            sent_at=datetime.utcnow(),
            status="sending",
        )
    )
    response = ses_complaint_callback()
    assert process_ses_results(response)

    assert send_mock.call_count == 1
    assert signer_complaint.verify(send_mock.call_args[0][0][0]) == {
        "complaint_date": "2018-06-05T13:59:58.000000Z",
        "complaint_id": str(Complaint.query.one().id),
        "notification_id": str(notification.id),
        "reference": None,
        "service_callback_api_bearer_token": "some_super_secret",
        "service_callback_api_url": "https://original_url.com",
        "to": "recipient1@example.com",
    }


class TestBounceRates:
    @pytest.mark.parametrize(
        "bounce_subtype, expected_subtype",
        [
            ("General", NOTIFICATION_HARD_GENERAL),
            ("NoEmail", NOTIFICATION_HARD_NOEMAIL),
            ("Suppressed", NOTIFICATION_HARD_SUPPRESSED),
            ("OnAccountSuppressionList", NOTIFICATION_HARD_ONACCOUNTSUPPRESSIONLIST),
        ],
    )
    def test_ses_callback_should_update_bounce_info_new_delivery_receipt_hard_bounce(
        self, sample_email_template, mocker, bounce_subtype, expected_subtype
    ):
        notification = save_notification(create_notification(template=sample_email_template, reference="ref", status="delivered"))

        assert process_ses_results(ses_hard_bounce_callback(reference="ref", bounce_subtype=bounce_subtype))
        assert get_notification_by_id(notification.id).feedback_type == NOTIFICATION_HARD_BOUNCE
        assert get_notification_by_id(notification.id).feedback_subtype == expected_subtype

    @pytest.mark.parametrize(
        "bounce_subtype, expected_subtype",
        [
            ("General", NOTIFICATION_SOFT_GENERAL),
            ("MailboxFull", NOTIFICATION_SOFT_MAILBOXFULL),
            ("MessageTooLarge", NOTIFICATION_SOFT_MESSAGETOOLARGE),
            ("ContentRejected", NOTIFICATION_SOFT_CONTENTREJECTED),
            ("AttachmentRejected", NOTIFICATION_SOFT_ATTACHMENTREJECTED),
        ],
    )
    def test_ses_callback_should_update_bounce_info_new_delivery_receipt_soft_bounce(
        self, sample_email_template, mocker, bounce_subtype, expected_subtype
    ):
        notification = save_notification(create_notification(template=sample_email_template, reference="ref", status="delivered"))

        assert process_ses_results(ses_soft_bounce_callback(reference="ref", bounce_subtype=bounce_subtype))
        assert get_notification_by_id(notification.id).feedback_type == NOTIFICATION_SOFT_BOUNCE
        assert get_notification_by_id(notification.id).feedback_subtype == expected_subtype

    @pytest.mark.parametrize(
        "bounce_subtype, expected_subtype",
        [
            ("General", NOTIFICATION_HARD_GENERAL),
            ("NoEmail", NOTIFICATION_HARD_NOEMAIL),
            ("Suppressed", NOTIFICATION_HARD_SUPPRESSED),
            ("OnAccountSuppressionList", NOTIFICATION_HARD_ONACCOUNTSUPPRESSIONLIST),
        ],
    )
    def test_ses_callback_should_add_redis_key_when_delivery_receipt_is_hard_bounce(
        self, sample_email_template, mocker, bounce_subtype, expected_subtype
    ):
        mocker.patch("app.bounce_rate_client.set_sliding_hard_bounce")
        mocker.patch("app.bounce_rate_client.set_sliding_notifications")

        notification = save_notification(create_notification(template=sample_email_template, reference="ref", status="delivered"))

        assert process_ses_results(ses_hard_bounce_callback(reference="ref", bounce_subtype=bounce_subtype))

        bounce_rate_client.set_sliding_hard_bounce.assert_called_with(notification.service_id, str(notification.id))
        bounce_rate_client.set_sliding_notifications.assert_not_called()

    @pytest.mark.parametrize(
        "bounce_subtype, expected_subtype",
        [
            ("General", NOTIFICATION_SOFT_GENERAL),
            ("MailboxFull", NOTIFICATION_SOFT_MAILBOXFULL),
            ("MessageTooLarge", NOTIFICATION_SOFT_MESSAGETOOLARGE),
            ("ContentRejected", NOTIFICATION_SOFT_CONTENTREJECTED),
            ("AttachmentRejected", NOTIFICATION_SOFT_ATTACHMENTREJECTED),
        ],
    )
    def test_ses_callback_should_not_add_redis_keys_when_delivery_receipt_is_soft_bounce(
        self, sample_email_template, mocker, bounce_subtype, expected_subtype
    ):
        mocker.patch("app.bounce_rate_client.set_sliding_hard_bounce")
        mocker.patch("app.bounce_rate_client.set_sliding_notifications")

        save_notification(create_notification(template=sample_email_template, reference="ref", status="delivered"))

        assert process_ses_results(ses_soft_bounce_callback(reference="ref", bounce_subtype=bounce_subtype))

        bounce_rate_client.set_sliding_hard_bounce.assert_not_called()
        bounce_rate_client.set_sliding_notifications.assert_not_called()


class TestAnnualLimits:
    def test_ses_callback_should_increment_email_delivered_when_delivery_receipt_is_delivered(
        self, notify_api, sample_email_template, mocker
    ):
        mocker.patch("app.annual_limit_client.increment_email_delivered")
        mocker.patch("app.annual_limit_client.increment_email_failed")
        mocker.patch("app.annual_limit_client.was_seeded_today", return_value=True)

        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            save_notification(create_notification(template=sample_email_template, reference="ref", status="sending"))

            assert process_ses_results(ses_notification_callback(reference="ref"))
            annual_limit_client.increment_email_delivered.assert_called_once_with(sample_email_template.service_id)
            annual_limit_client.increment_email_failed.assert_not_called()

    @pytest.mark.parametrize(
        "callback, bounce_type",
        [
            (ses_hard_bounce_callback, NOTIFICATION_HARD_BOUNCE),
            (ses_soft_bounce_callback, NOTIFICATION_SOFT_BOUNCE),
            (ses_unknown_bounce_callback, NOTIFICATION_UNKNOWN_BOUNCE),
        ],
    )
    def test_ses_callback_should_increment_email_failed_when_delivery_receipt_is_failure(
        self, notify_api, sample_email_template, mocker, callback, bounce_type
    ):
        mocker.patch("app.annual_limit_client.increment_email_failed")
        mocker.patch("app.annual_limit_client.increment_email_delivered")
        mocker.patch("app.annual_limit_client.was_seeded_today", return_value=True)

        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            save_notification(create_notification(template=sample_email_template, reference="ref", status="sending"))

            assert process_ses_results(callback(reference="ref"))
            annual_limit_client.increment_email_failed.assert_called_once_with(sample_email_template.service_id)
            annual_limit_client.increment_email_delivered.assert_not_called()

    @pytest.mark.parametrize(
        "callback",
        [
            ses_notification_callback,
            ses_hard_bounce_callback,
            ses_soft_bounce_callback,
        ],
    )
    def test_process_ses_results_seeds_annual_limit_notifications_when_not_seeded_today_and_doesnt_increment_when_seeding(
        self,
        callback,
        sample_email_template,
        notify_api,
        mocker,
    ):
        mocker.patch("app.annual_limit_client.increment_email_delivered")
        mocker.patch("app.annual_limit_client.increment_email_failed")
        mocker.patch("app.annual_limit_client.was_seeded_today", return_value=False)
        mocker.patch("app.annual_limit_client.set_seeded_at")

        notification = save_notification(
            create_notification(
                sample_email_template,
                reference="ref",
                sent_at=datetime.utcnow(),
                status="sending",
                sent_by="ses",
            )
        )
        # TODO FF_ANNUAL_LIMIT removal
        with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
            process_ses_results(callback(reference="ref"))
            annual_limit_client.set_seeded_at.assert_called_once_with(notification.service_id)
            annual_limit_client.increment_email_delivered.assert_not_called()
            annual_limit_client.increment_email_failed.assert_not_called()
