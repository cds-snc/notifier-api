import json
import uuid
from datetime import datetime, timedelta
from unittest import mock
from unittest.mock import Mock, call

import pytest
import requests_mock
from freezegun import freeze_time
from notifications_utils.columns import Row
from notifications_utils.recipients import RecipientCSV
from notifications_utils.template import SMSMessageTemplate, WithSubjectTemplate
from requests import RequestException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import DATETIME_FORMAT, redis_store, signer
from app.celery import provider_tasks, tasks
from app.celery.tasks import (
    acknowledge_receipt,
    choose_database_queue,
    get_template_class,
    handle_batch_error_and_forward,
    process_incomplete_job,
    process_incomplete_jobs,
    process_job,
    process_rows,
    s3,
    save_emails,
    save_smss,
    send_inbound_sms_to_service,
    send_notify_no_reply,
)
from app.config import QueueNames
from app.dao import jobs_dao, service_email_reply_to_dao, service_sms_sender_dao
from app.dao.services_dao import dao_fetch_service_by_id
from app.models import (
    BULK,
    EMAIL_TYPE,
    JOB_STATUS_ERROR,
    JOB_STATUS_FINISHED,
    JOB_STATUS_IN_PROGRESS,
    KEY_TYPE_NORMAL,
    LETTER_TYPE,
    NORMAL,
    PRIORITY,
    SMS_TYPE,
    Job,
    Notification,
    ServiceEmailReplyTo,
    ServiceSmsSender,
)
from app.schemas import service_schema, template_schema
from celery.exceptions import Retry
from tests.app import load_example_csv
from tests.app.conftest import create_sample_service, create_sample_template
from tests.app.db import (
    create_inbound_sms,
    create_job,
    create_notification,
    create_reply_to_email,
    create_service,
    create_service_inbound_api,
    create_service_with_defined_sms_sender,
    create_template,
    create_user,
    save_notification,
)
from tests.conftest import set_config_values


class AnyStringWith(str):
    def __eq__(self, other):
        return self in other


def _notification_json(template, to, personalisation=None, job_id=None, row_number=0, queue=None, reply_to_text=None):
    return {
        "template": str(template.id),
        "template_version": template.version,
        "to": to,
        "notification_type": template.template_type,
        "personalisation": personalisation or {},
        "job": job_id and str(job_id),
        "row_number": row_number,
        "queue": queue,
        "reply_to_text": reply_to_text,
    }


def test_should_have_decorated_tasks_functions():
    assert process_job.__wrapped__.__name__ == "process_job"
    assert save_smss.__wrapped__.__name__ == "save_smss"
    assert save_emails.__wrapped__.__name__ == "save_emails"


class TestAcknowledgeReceipt:
    def test_acknowledge_happy_path(self, mocker):
        receipt = uuid.uuid4()
        acknowledge_sms_normal_mock = mocker.patch("app.sms_normal.acknowledge", return_value=True)
        acknowledge_sms_priority_mock = mocker.patch("app.sms_bulk.acknowledge", return_value=False)
        acknowledge_receipt(SMS_TYPE, NORMAL, receipt)
        assert acknowledge_sms_normal_mock.called_once_with(receipt)
        assert acknowledge_sms_priority_mock.not_called()

    def test_acknowledge_wrong_queue(self, mocker, notify_api):
        receipt = uuid.uuid4()
        acknowledge_sms_bulk_mock = mocker.patch("app.sms_bulk.acknowledge", return_value=True)
        acknowledge_receipt(EMAIL_TYPE, NORMAL, receipt)
        assert acknowledge_sms_bulk_mock.called_once_with(receipt)

    def test_acknowledge_no_queue(self):
        with pytest.raises(ValueError):
            acknowledge_receipt(None, None, uuid.uuid4())


@pytest.fixture
def email_job_with_placeholders(notify_db, notify_db_session, sample_email_template_with_placeholders):
    return create_job(template=sample_email_template_with_placeholders)


class TestChooseDatabaseQueue:
    @pytest.mark.parametrize(
        "research_mode,template_priority",
        [(True, PRIORITY), (True, NORMAL), (True, BULK), (False, PRIORITY), (False, NORMAL), (False, BULK)],
    )
    def test_choose_database_queue_FF_PRIORITY_LANES_true(
        self, mocker, notify_db, notify_db_session, notify_api, research_mode, template_priority
    ):
        service = create_sample_service(notify_db, notify_db_session, research_mode=research_mode)
        template = create_sample_template(notify_db, notify_db_session, process_type=template_priority)

        if research_mode:
            expected_queue = QueueNames.RESEARCH_MODE
        elif template_priority == PRIORITY:
            expected_queue = QueueNames.PRIORITY_DATABASE
        elif template_priority == NORMAL:
            expected_queue = QueueNames.NORMAL_DATABASE
        elif template_priority == BULK:
            expected_queue = QueueNames.BULK_DATABASE

        actual_queue = choose_database_queue(template, service)

        assert expected_queue == actual_queue


@pytest.mark.usefixtures("notify_db_session")
class TestBatchSaving:
    def test_save_emails(self, notify_db_session, mocker):
        service = create_service(research_mode=True)

        template = create_template(service=service, template_type="email")

        notification1 = _notification_json(template, to="test1@test.com")
        notification2 = _notification_json(template, to="test2@test.com")
        notification3 = _notification_json(template, to="test3@test.com")

        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        save_emails(
            str(template.service_id),
            [signer.sign(notification1), signer.sign(notification2), signer.sign(notification3)],
            None,
        )

        persisted_notification = Notification.query.all()
        assert persisted_notification[0].to == "test1@test.com"
        assert persisted_notification[1].to == "test2@test.com"
        assert persisted_notification[2].to == "test3@test.com"
        assert persisted_notification[0].template_id == template.id
        assert persisted_notification[1].template_version == template.version
        assert persisted_notification[0].status == "created"
        assert persisted_notification[0].notification_type == "email"

    def test_should_save_smss(self, sample_template_with_placeholders, mocker):
        notification1 = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2221",
            personalisation={"name": "Jo"},
        )
        notification1_id = uuid.uuid4()
        notification1["id"] = str(notification1_id)

        notification2 = _notification_json(
            sample_template_with_placeholders, to="+1 650 253 2222", personalisation={"name": "Test2"}
        )

        notification3 = _notification_json(
            sample_template_with_placeholders, to="+1 650 253 2223", personalisation={"name": "Test3"}
        )

        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")
        acknowledge_mock = mocker.patch("app.sms_normal.acknowledge")

        receipt = uuid.uuid4()
        save_smss(
            str(sample_template_with_placeholders.service.id),
            [signer.sign(notification1), signer.sign(notification2), signer.sign(notification3)],
            receipt,
        )

        persisted_notification = Notification.query.all()
        assert persisted_notification[0].id == notification1_id
        assert persisted_notification[0].to == "+1 650 253 2221"
        assert persisted_notification[1].to == "+1 650 253 2222"
        assert persisted_notification[2].to == "+1 650 253 2223"
        assert persisted_notification[0].template_id == sample_template_with_placeholders.id
        assert persisted_notification[1].template_version == sample_template_with_placeholders.version
        assert persisted_notification[0].status == "created"
        assert persisted_notification[0].personalisation == {"name": "Jo"}
        assert persisted_notification[0]._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification[0].notification_type == SMS_TYPE

        acknowledge_mock.assert_called_once_with(receipt)

    def test_should_save_smss_acknowledge_queue(self, sample_template_with_placeholders, notify_api, mocker):
        notification1 = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2221",
            personalisation={"name": "Jo"},
        )
        notification1_id = uuid.uuid4()
        notification1["id"] = str(notification1_id)

        notification2 = _notification_json(
            sample_template_with_placeholders, to="+1 650 253 2222", personalisation={"name": "Test2"}
        )

        notification3 = _notification_json(
            sample_template_with_placeholders, to="+1 650 253 2223", personalisation={"name": "Test3"}
        )

        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")
        acknowldege_mock = mocker.patch("app.sms_normal.acknowledge")

        receipt = uuid.uuid4()
        save_smss(
            str(sample_template_with_placeholders.service.id),
            [signer.sign(notification1), signer.sign(notification2), signer.sign(notification3)],
            receipt,
        )

        persisted_notification = Notification.query.all()
        assert persisted_notification[0].id == notification1_id
        assert persisted_notification[0].to == "+1 650 253 2221"
        assert persisted_notification[1].to == "+1 650 253 2222"
        assert persisted_notification[2].to == "+1 650 253 2223"
        assert persisted_notification[0].template_id == sample_template_with_placeholders.id
        assert persisted_notification[1].template_version == sample_template_with_placeholders.version
        assert persisted_notification[0].status == "created"
        assert persisted_notification[0].personalisation == {"name": "Jo"}
        assert persisted_notification[0]._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification[0].notification_type == SMS_TYPE

        acknowldege_mock.assert_called_once_with(receipt)

    def test_should_save_emails(self, sample_email_template_with_placeholders, mocker):
        notification1 = _notification_json(
            sample_email_template_with_placeholders,
            to="test1@gmail.com",
            personalisation={"name": "Jo"},
        )
        notification1_id = uuid.uuid4()
        notification1["id"] = str(notification1_id)

        notification2 = _notification_json(
            sample_email_template_with_placeholders, to="test2@gmail.com", personalisation={"name": "Test2"}
        )

        notification3 = _notification_json(
            sample_email_template_with_placeholders, to="test3@gmail.com", personalisation={"name": "Test3"}
        )

        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        acknowledge_mock = mocker.patch("app.email_normal.acknowledge")

        receipt = uuid.uuid4()

        save_emails(
            str(sample_email_template_with_placeholders.service.id),
            [signer.sign(notification1), signer.sign(notification2), signer.sign(notification3)],
            receipt,
        )

        persisted_notification = Notification.query.all()
        assert persisted_notification[0].id == notification1_id
        assert persisted_notification[0].to == "test1@gmail.com"
        assert persisted_notification[1].to == "test2@gmail.com"
        assert persisted_notification[2].to == "test3@gmail.com"
        assert persisted_notification[0].template_id == sample_email_template_with_placeholders.id
        assert persisted_notification[1].template_version == sample_email_template_with_placeholders.version
        assert persisted_notification[0].status == "created"
        assert persisted_notification[0].personalisation == {"name": "Jo"}
        assert persisted_notification[0]._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification[0].notification_type == EMAIL_TYPE

        acknowledge_mock.assert_called_once_with(receipt)

    def test_should_not_forward_sms_on_duplicate(self, sample_template_with_placeholders, mocker):
        notification1 = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2221",
            personalisation={"name": "Jo"},
        )
        notification1["id"] = str(uuid.uuid4())
        notification1["service_id"] = str(sample_template_with_placeholders.service.id)

        mock_deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")
        mock_persist_notifications = mocker.patch(
            "app.celery.tasks.persist_notifications", side_effect=IntegrityError(None, None, None)
        )
        mock_get_notification = mocker.patch("app.celery.tasks.get_notification_by_id", return_value=notification1)
        mock_save_sms = mocker.patch("app.celery.tasks.save_smss.apply_async")
        mock_acknowldege = mocker.patch("app.sms_normal.acknowledge")

        receipt = uuid.uuid4()
        notifications = [signer.sign(notification1)]

        save_smss(
            None,
            notifications,
            receipt,
        )

        mock_deliver_sms.assert_not_called()
        mock_persist_notifications.assert_called_once()
        mock_get_notification.assert_called_once_with(notification1["id"])
        mock_save_sms.assert_not_called()
        mock_acknowldege.assert_called_once_with(receipt)

    def test_should_not_forward_email_on_duplicate(self, sample_email_template_with_placeholders, mocker):
        notification1 = _notification_json(
            sample_email_template_with_placeholders,
            to="test1@gmail.com",
            personalisation={"name": "Jo"},
        )
        notification1["id"] = str(uuid.uuid4())
        notification1["service_id"] = str(sample_email_template_with_placeholders.service.id)

        mock_deliver_email = mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        mock_persist_notifications = mocker.patch(
            "app.celery.tasks.persist_notifications", side_effect=IntegrityError(None, None, None)
        )
        mock_get_notification = mocker.patch("app.celery.tasks.get_notification_by_id", return_value=notification1)
        mock_save_email = mocker.patch("app.celery.tasks.save_emails.apply_async")
        mock_acknowldege = mocker.patch("app.email_normal.acknowledge")

        receipt = uuid.uuid4()
        notifications = [signer.sign(notification1)]

        save_emails(
            None,
            notifications,
            receipt,
        )

        mock_deliver_email.assert_not_called()
        mock_persist_notifications.assert_called_once()
        mock_get_notification.assert_called_once_with(notification1["id"])
        mock_save_email.assert_not_called()
        mock_acknowldege.assert_called_once_with(receipt)

    def test_should_process_smss_job_metric_check(self, mocker):
        pbsbc_mock = mocker.patch("app.celery.tasks.put_batch_saving_bulk_created")
        service = create_service(message_limit=20)
        template = create_template(service=service)
        job = create_job(template=template, notification_count=10, original_file_name="multiple_sms.csv")
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mocker.patch("app.celery.tasks.save_smss.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        redis_mock = mocker.patch("app.celery.tasks.statsd_client.timing_with_dates")

        process_job(job.id)

        s3.get_job_from_s3.assert_called_once_with(str(job.service.id), str(job.id))

        assert signer.sign.call_args[0][0]["to"] == "+441234123120"
        assert signer.sign.call_args[0][0]["template"] == str(template.id)
        assert signer.sign.call_args[0][0]["template_version"] == template.version
        assert signer.sign.call_args[0][0]["personalisation"] == {
            "phonenumber": "+441234123120",
        }
        tasks.save_smss.apply_async.assert_called_once_with(
            (
                str(job.service_id),
                [
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                ],
                None,
            ),
            queue="-normal-database-tasks",
        )
        job = jobs_dao.dao_get_job_by_id(job.id)
        assert job.job_status == "finished"
        assert job.processing_started is not None
        assert job.created_at is not None
        redis_mock.assert_called_once_with("job.processing-start-delay", job.processing_started, job.created_at)
        assert pbsbc_mock.assert_called_with(mock.ANY, 1, notification_type="sms", priority="normal") is None

    def test_process_smss_job_metric(self, sample_template_with_placeholders, mocker):
        pbsbp_mock = mocker.patch("app.celery.tasks.put_batch_saving_bulk_processed")
        notification1 = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2221",
            personalisation={"name": "Jo"},
        )
        notification1_id = uuid.uuid4()
        notification1["id"] = str(notification1_id)

        notification2 = _notification_json(
            sample_template_with_placeholders, to="+1 650 253 2222", personalisation={"name": "Test2"}
        )

        notification3 = _notification_json(
            sample_template_with_placeholders, to="+1 650 253 2223", personalisation={"name": "Test3"}
        )

        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        save_smss(
            str(sample_template_with_placeholders.service.id),
            [signer.sign(notification1), signer.sign(notification2), signer.sign(notification3)],
            None,
        )

        persisted_notification = Notification.query.all()
        assert persisted_notification[0].id == notification1_id
        assert persisted_notification[0].to == "+1 650 253 2221"
        assert persisted_notification[1].to == "+1 650 253 2222"
        assert persisted_notification[2].to == "+1 650 253 2223"
        assert persisted_notification[0].template_id == sample_template_with_placeholders.id
        assert persisted_notification[1].template_version == sample_template_with_placeholders.version
        assert persisted_notification[0].status == "created"
        assert persisted_notification[0].personalisation == {"name": "Jo"}
        assert persisted_notification[0]._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification[0].notification_type == SMS_TYPE
        assert pbsbp_mock.assert_called_with(mock.ANY, 1, notification_type="sms", priority="normal") is None


class TestProcessJob:
    def test_should_process_sms_job_FF_PRIORITY_LANES_true(self, sample_job, mocker):
        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=load_example_csv("sms"))
        mocker.patch("app.celery.tasks.save_smss.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")

        redis_mock = mocker.patch("app.celery.tasks.statsd_client.timing_with_dates")

        process_job(sample_job.id)
        s3.get_job_from_s3.assert_called_once_with(str(sample_job.service.id), str(sample_job.id))
        assert signer.sign.call_args[0][0]["to"] == "+441234123123"
        assert signer.sign.call_args[0][0]["template"] == str(sample_job.template.id)
        assert signer.sign.call_args[0][0]["template_version"] == sample_job.template.version
        assert signer.sign.call_args[0][0]["personalisation"] == {"phonenumber": "+441234123123"}
        assert signer.sign.call_args[0][0]["row_number"] == 0
        tasks.save_smss.apply_async.assert_called_once_with(
            (str(sample_job.service_id), ["something_encrypted"], None), queue=QueueNames.NORMAL_DATABASE
        )
        job = jobs_dao.dao_get_job_by_id(sample_job.id)
        assert job.job_status == "finished"
        assert job.processing_started is not None
        assert job.created_at is not None
        redis_mock.assert_called_once_with("job.processing-start-delay", job.processing_started, job.created_at)

    def test_should_process_sms_job_with_sender_id(self, sample_template, mocker, fake_uuid):
        job = create_job(template=sample_template, sender_id=fake_uuid)
        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=load_example_csv("sms"))
        mocker.patch("app.celery.tasks.save_smss.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")

        process_job(job.id)

        tasks.save_smss.apply_async.assert_called_once_with(
            (str(job.service_id), ["something_encrypted"], None),
            queue="-normal-database-tasks",
        )

    @freeze_time("2016-01-01 11:09:00.061258")
    def test_should_not_process_sms_job_if_would_exceed_send_limits(self, notify_db_session, mocker):
        service = create_service(message_limit=9)
        template = create_template(service=service)
        job = create_job(template=template, notification_count=10, original_file_name="multiple_sms.csv")
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mocker.patch("app.celery.tasks.process_rows")

        process_job(job.id)

        job = jobs_dao.dao_get_job_by_id(job.id)
        assert job.job_status == "sending limits exceeded"
        assert s3.get_job_from_s3.called is False
        assert tasks.process_rows.called is False

    def test_should_not_process_sms_job_if_would_exceed_send_limits_inc_today(self, notify_db_session, mocker):
        service = create_service(message_limit=1)
        template = create_template(service=service)
        job = create_job(template=template)

        save_notification(create_notification(template=template, job=job))

        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=load_example_csv("sms"))
        mocker.patch("app.celery.tasks.process_rows")

        process_job(job.id)

        job = jobs_dao.dao_get_job_by_id(job.id)
        assert job.job_status == "sending limits exceeded"
        assert s3.get_job_from_s3.called is False
        assert tasks.process_rows.called is False

    @pytest.mark.parametrize("template_type", ["sms", "email"])
    def test_should_not_process_email_job_if_would_exceed_send_limits_inc_today(self, notify_db_session, template_type, mocker):
        service = create_service(message_limit=1)
        template = create_template(service=service, template_type=template_type)
        job = create_job(template=template)

        save_notification(create_notification(template=template, job=job))

        mocker.patch("app.celery.tasks.s3.get_job_from_s3")
        mocker.patch("app.celery.tasks.process_rows")

        process_job(job.id)

        job = jobs_dao.dao_get_job_by_id(job.id)
        assert job.job_status == "sending limits exceeded"
        assert s3.get_job_from_s3.called is False
        assert tasks.process_rows.called is False

    def test_should_not_process_job_if_already_pending(self, sample_template, mocker):
        job = create_job(template=sample_template, job_status="scheduled")

        mocker.patch("app.celery.tasks.s3.get_job_from_s3")
        mocker.patch("app.celery.tasks.process_rows")

        process_job(job.id)

        assert s3.get_job_from_s3.called is False
        assert tasks.process_rows.called is False

    def test_should_process_email_job_if_exactly_on_send_limits(self, notify_db_session, mocker):
        service = create_service(message_limit=10)
        template = create_template(service=service, template_type="email")
        job = create_job(template=template, notification_count=10)

        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_email"),
        )
        mocker.patch("app.celery.tasks.save_emails.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")

        process_job(job.id)

        s3.get_job_from_s3.assert_called_once_with(str(job.service.id), str(job.id))
        job = jobs_dao.dao_get_job_by_id(job.id)
        assert job.job_status == "finished"
        tasks.save_emails.apply_async.assert_called_with(
            (
                str(job.service_id),
                [
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                ],
                None,
            ),
            queue="-normal-database-tasks",
        )

    def test_should_process_smss_job(self, notify_db_session, mocker):
        service = create_service(message_limit=20)
        template = create_template(service=service)
        job = create_job(template=template, notification_count=10, original_file_name="multiple_sms.csv")
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mocker.patch("app.celery.tasks.save_smss.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        redis_mock = mocker.patch("app.celery.tasks.statsd_client.timing_with_dates")

        process_job(job.id)

        s3.get_job_from_s3.assert_called_once_with(str(job.service.id), str(job.id))

        assert signer.sign.call_args[0][0]["to"] == "+441234123120"
        assert signer.sign.call_args[0][0]["template"] == str(template.id)
        assert signer.sign.call_args[0][0]["template_version"] == template.version
        assert signer.sign.call_args[0][0]["personalisation"] == {
            "phonenumber": "+441234123120",
        }
        tasks.save_smss.apply_async.assert_called_once_with(
            (
                str(job.service_id),
                [
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                    "something_encrypted",
                ],
                None,
            ),
            queue="-normal-database-tasks",
        )
        job = jobs_dao.dao_get_job_by_id(job.id)
        assert job.job_status == "finished"
        assert job.processing_started is not None
        assert job.created_at is not None
        redis_mock.assert_called_once_with("job.processing-start-delay", job.processing_started, job.created_at)

    def test_should_not_create_save_task_for_empty_file(self, sample_job, mocker):
        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=load_example_csv("empty"))
        mocker.patch("app.celery.tasks.save_smss.apply_async")

        process_job(sample_job.id)

        s3.get_job_from_s3.assert_called_once_with(str(sample_job.service.id), str(sample_job.id))
        job = jobs_dao.dao_get_job_by_id(sample_job.id)
        assert job.job_status == "finished"
        assert tasks.save_smss.apply_async.called is False

    def test_should_process_email_job(self, email_job_with_placeholders, mocker):
        email_csv = """email_address,name
        test@test.com,foo
        """
        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=email_csv)
        mocker.patch("app.celery.tasks.save_emails.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")
        redis_mock = mocker.patch("app.celery.tasks.statsd_client.timing_with_dates")

        process_job(email_job_with_placeholders.id)

        s3.get_job_from_s3.assert_called_once_with(
            str(email_job_with_placeholders.service.id), str(email_job_with_placeholders.id)
        )
        assert signer.sign.call_args[0][0]["to"] == "test@test.com"
        assert signer.sign.call_args[0][0]["template"] == str(email_job_with_placeholders.template.id)
        assert signer.sign.call_args[0][0]["template_version"] == email_job_with_placeholders.template.version
        assert signer.sign.call_args[0][0]["personalisation"] == {
            "emailaddress": "test@test.com",
            "name": "foo",
        }
        tasks.save_emails.apply_async.assert_called_once_with(
            (str(email_job_with_placeholders.service_id), ["something_encrypted"], None),
            queue="-normal-database-tasks",
        )
        job = jobs_dao.dao_get_job_by_id(email_job_with_placeholders.id)
        assert job.job_status == "finished"
        assert job.processing_started is not None
        assert job.created_at is not None
        redis_mock.assert_called_once_with("job.processing-start-delay", job.processing_started, job.created_at)

    def test_should_process_emails_job(self, email_job_with_placeholders, mocker):
        email_csv = """email_address,name
        test@test.com,foo
        YOLO@test2.com,foo2
        yolo2@test2.com,foo3
        yolo3@test3.com,foo4
        """
        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=email_csv)
        mocker.patch("app.celery.tasks.save_emails.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        redis_mock = mocker.patch("app.celery.tasks.statsd_client.timing_with_dates")

        process_job(email_job_with_placeholders.id)

        s3.get_job_from_s3.assert_called_once_with(
            str(email_job_with_placeholders.service.id), str(email_job_with_placeholders.id)
        )

        assert signer.sign.call_args[0][0]["to"] == "yolo3@test3.com"
        assert signer.sign.call_args[0][0]["template"] == str(email_job_with_placeholders.template.id)
        assert signer.sign.call_args[0][0]["template_version"] == email_job_with_placeholders.template.version
        assert signer.sign.call_args[0][0]["personalisation"] == {
            "emailaddress": "yolo3@test3.com",
            "name": "foo4",
        }
        tasks.save_emails.apply_async.assert_called_once_with(
            (
                str(email_job_with_placeholders.service_id),
                ["something_encrypted", "something_encrypted", "something_encrypted", "something_encrypted"],
                None,
            ),
            queue="-normal-database-tasks",
        )
        job = jobs_dao.dao_get_job_by_id(email_job_with_placeholders.id)
        assert job.job_status == "finished"
        assert job.processing_started is not None
        assert job.created_at is not None
        redis_mock.assert_called_once_with("job.processing-start-delay", job.processing_started, job.created_at)

    def test_should_process_email_job_with_sender_id(self, sample_email_template, mocker, fake_uuid):
        email_csv = """email_address,name
        test@test.com,foo
        """
        job = create_job(template=sample_email_template, sender_id=fake_uuid)
        mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=email_csv)
        mocker.patch("app.celery.tasks.save_emails.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")

        process_job(job.id)

        tasks.save_emails.apply_async.assert_called_once_with(
            (str(job.service_id), ["something_encrypted"], None), queue="-normal-database-tasks"
        )

    @pytest.mark.skip(reason="the code paths don't exist for letter implementation")
    @freeze_time("2016-01-01 11:09:00.061258")
    def test_should_process_letter_job(self, sample_letter_job, mocker):
        csv = """address_line_1,address_line_2,address_line_3,address_line_4,postcode,name
        A1,A2,A3,A4,A_POST,Alice
        """
        s3_mock = mocker.patch("app.celery.tasks.s3.get_job_from_s3", return_value=csv)
        process_row_mock = mocker.patch("app.celery.tasks.process_row")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")

        process_job(sample_letter_job.id)

        s3_mock.assert_called_once_with(str(sample_letter_job.service.id), str(sample_letter_job.id))

        row_call = process_row_mock.mock_calls[0][1]
        assert row_call[0].index == 0
        assert row_call[0].recipient == ["A1", "A2", "A3", "A4", None, None, "A_POST"]
        assert row_call[0].personalisation == {
            "addressline1": "A1",
            "addressline2": "A2",
            "addressline3": "A3",
            "addressline4": "A4",
            "postcode": "A_POST",
        }
        assert row_call[2] == sample_letter_job
        assert row_call[3] == sample_letter_job.service

        assert process_row_mock.call_count == 1

        assert sample_letter_job.job_status == "finished"

    def test_should_process_all_sms_job(self, sample_job_with_placeholdered_template, mocker):
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mocker.patch("app.celery.tasks.save_smss.apply_async")
        mocker.patch("app.encryption.CryptoSigner.sign", return_value="something_encrypted")
        mocker.patch("app.celery.tasks.create_uuid", return_value="uuid")

        process_job(sample_job_with_placeholdered_template.id)

        s3.get_job_from_s3.assert_called_once_with(
            str(sample_job_with_placeholdered_template.service.id),
            str(sample_job_with_placeholdered_template.id),
        )
        assert signer.sign.call_args[0][0]["to"] == "+441234123120"
        assert signer.sign.call_args[0][0]["template"] == str(sample_job_with_placeholdered_template.template.id)
        assert signer.sign.call_args[0][0]["template_version"] == sample_job_with_placeholdered_template.template.version  # noqa
        assert signer.sign.call_args[0][0]["personalisation"] == {
            "phonenumber": "+441234123120",
            "name": "chris",
        }
        assert tasks.save_smss.apply_async.call_count == 1
        job = jobs_dao.dao_get_job_by_id(sample_job_with_placeholdered_template.id)
        assert job.job_status == "finished"

    def test_should_cancel_job_if_service_is_inactive(self, sample_service, sample_job, mocker):
        sample_service.active = False

        mocker.patch("app.celery.tasks.s3.get_job_from_s3")
        mocker.patch("app.celery.tasks.process_rows")

        process_job(sample_job.id)

        job = jobs_dao.dao_get_job_by_id(sample_job.id)
        assert job.job_status == "cancelled"
        s3.get_job_from_s3.assert_not_called()
        tasks.process_rows.assert_not_called()


class TestProcessRows:
    @pytest.mark.parametrize(
        "template_type, research_mode, expected_function, expected_queue, api_key_id, sender_id, reference",
        [
            (SMS_TYPE, False, "save_smss", "-normal-database-tasks", None, None, None),
            (SMS_TYPE, True, "save_smss", "research-mode-tasks", uuid.uuid4(), uuid.uuid4(), "ref1"),
            (EMAIL_TYPE, False, "save_emails", "-normal-database-tasks", uuid.uuid4(), uuid.uuid4(), "ref2"),
            (EMAIL_TYPE, True, "save_emails", "research-mode-tasks", None, None, None),
        ],
    )
    def test_process_rows_sends_save_task(
        self,
        notify_api,
        template_type,
        research_mode,
        expected_function,
        expected_queue,
        api_key_id,
        sender_id,
        reference,
        mocker,
    ):
        mocker.patch("app.celery.tasks.create_uuid", return_value="noti_uuid")
        task_mock = mocker.patch("app.celery.tasks.{}".format(expected_function))
        signer_mock = mocker.patch("app.celery.tasks.signer.sign")
        template = Mock(id="template_id", template_type=template_type)
        job = Mock(id="job_id", template_version="temp_vers", notification_count=1, api_key_id=api_key_id, sender_id=sender_id)
        service = Mock(id="service_id", research_mode=research_mode)

        process_rows(
            [
                Row(
                    {"foo": "bar", "to": "recip", "reference": reference} if reference else {"foo": "bar", "to": "recip"},
                    index="row_num",
                    error_fn=lambda k, v: None,
                    recipient_column_headers=["to"],
                    placeholders={"foo"},
                    template=template,
                )
            ],
            template,
            job,
            service,
        )
        signer_mock.assert_called_once_with(
            {
                "api_key": None if api_key_id is None else str(api_key_id),
                "key_type": job.api_key.key_type,
                "template": "template_id",
                "template_version": "temp_vers",
                "job": "job_id",
                "to": "recip",
                "row_number": "row_num",
                "personalisation": {"foo": "bar"},
                "queue": None,
                "client_reference": reference,
                "sender_id": str(sender_id) if sender_id else None,
            }
        )
        task_mock.apply_async.assert_called_once()

    @pytest.mark.parametrize(
        "csv_threshold, expected_queue",
        [
            (0, "bulk-tasks"),
            (1_000, None),
        ],
    )
    def test_should_redirect_job_to_queue_depending_on_csv_threshold(
        self, notify_api, sample_job, mocker, fake_uuid, csv_threshold, expected_queue
    ):
        mock_save_email = mocker.patch("app.celery.tasks.save_emails")

        template = Mock(id=1, template_type=EMAIL_TYPE)
        api_key = Mock(id=1, key_type=KEY_TYPE_NORMAL)
        job = Mock(id=1, template_version="temp_vers", notification_count=1, api_key=api_key)
        service = Mock(id=1, research_mode=False)

        row = next(
            RecipientCSV(
                load_example_csv("email"),
                template_type=EMAIL_TYPE,
            ).get_rows()
        )

        with set_config_values(notify_api, {"CSV_BULK_REDIRECT_THRESHOLD": csv_threshold}):
            process_rows([row], template, job, service)

        tasks.save_emails.apply_async.assert_called_once()
        args = mock_save_email.method_calls[0].args
        signed_notification = [i for i in args[0]][1][0]
        notification = signer.verify(signed_notification)
        assert expected_queue == notification.get("queue")

    def test_should_not_save_sms_if_restricted_service_and_invalid_number(self, notify_db_session, mocker):
        user = create_user(mobile_number="6502532222")
        service = create_service(user=user, restricted=True)
        template = create_template(service=service)
        job = create_job(template)
        notification = _notification_json(template, to="07700 900849")

        save_sms_mock = mocker.patch("app.celery.tasks.save_smss")

        process_rows(
            [
                Row(
                    {"foo": "bar", "to": notification["to"]},
                    index="row_num",
                    error_fn=lambda k, v: None,
                    recipient_column_headers=["to"],
                    placeholders={"foo"},
                    template=SMSMessageTemplate(template.__dict__),
                )
            ],
            template,
            job,
            service,
        )

        assert not save_sms_mock.called

    @pytest.mark.parametrize(
        "template_type, research_mode, expected_function, expected_queue, api_key_id, sender_id, reference",
        [
            (SMS_TYPE, False, "save_smss", "-normal-database-tasks", None, None, None),
            (SMS_TYPE, True, "save_smss", "research-mode-tasks", uuid.uuid4(), uuid.uuid4(), "ref1"),
            (EMAIL_TYPE, False, "save_emails", "-normal-database-tasks", uuid.uuid4(), uuid.uuid4(), "ref2"),
            (EMAIL_TYPE, True, "save_emails", "research-mode-tasks", None, None, None),
        ],
    )
    def test_process_rows_works_without_key_type(
        self,
        notify_api,
        template_type,
        research_mode,
        expected_function,
        expected_queue,
        api_key_id,
        sender_id,
        reference,
        mocker,
    ):
        mocker.patch("app.celery.tasks.create_uuid", return_value="noti_uuid")
        task_mock = mocker.patch("app.celery.tasks.{}".format(expected_function))
        signer_mock = mocker.patch("app.celery.tasks.signer.sign")
        template = Mock(id="template_id", template_type=template_type)
        api_key = {}
        job = Mock(
            id="job_id",
            template_version="temp_vers",
            notification_count=1,
            api_key_id=api_key_id,
            sender_id=sender_id,
            api_key=api_key,
        )
        service = Mock(id="service_id", research_mode=research_mode)

        process_rows(
            [
                Row(
                    {"foo": "bar", "to": "recip", "reference": reference} if reference else {"foo": "bar", "to": "recip"},
                    index="row_num",
                    error_fn=lambda k, v: None,
                    recipient_column_headers=["to"],
                    placeholders={"foo"},
                    template=template,
                )
            ],
            template,
            job,
            service,
        )
        signer_mock.assert_called_once_with(
            {
                "api_key": None if api_key_id is None else str(api_key_id),
                "key_type": KEY_TYPE_NORMAL,
                "template": "template_id",
                "template_version": "temp_vers",
                "job": "job_id",
                "to": "recip",
                "row_number": "row_num",
                "personalisation": {"foo": "bar"},
                "queue": None,
                "client_reference": reference,
                "sender_id": str(sender_id) if sender_id else None,
            }
        )
        task_mock.apply_async.assert_called_once()


class TestSaveSmss:
    def test_should_send_template_to_correct_sms_task_and_persist(self, sample_template_with_placeholders, mocker):
        notification = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2222",
            personalisation={"name": "Jo"},
        )

        mocked_deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        save_smss(
            sample_template_with_placeholders.service_id,
            [signer.sign(notification)],
            uuid.uuid4(),
        )

        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "+1 650 253 2222"
        assert persisted_notification.template_id == sample_template_with_placeholders.id
        assert persisted_notification.template_version == sample_template_with_placeholders.version
        assert persisted_notification.status == "created"
        assert persisted_notification.created_at <= datetime.utcnow()
        assert not persisted_notification.sent_at
        assert not persisted_notification.sent_by
        assert not persisted_notification.job_id
        assert persisted_notification.personalisation == {"name": "Jo"}
        assert persisted_notification._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification.notification_type == "sms"
        mocked_deliver_sms.assert_called_once_with([str(persisted_notification.id)], queue="send-sms-tasks")

    @pytest.mark.parametrize("sender_id", [None, "996958a8-0c06-43be-a40e-56e4a2d1655c"])
    def test_save_sms_should_use_redis_cache_to_retrieve_service_and_template_when_possible(
        self, sample_template_with_placeholders, mocker, sample_service, sender_id
    ):
        notification = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2222",
            personalisation={"name": "Jo"},
        )
        if sender_id:
            notification["sender_id"] = sender_id

        sms_sender = ServiceSmsSender()
        sms_sender.sms_sender = "+16502532222"
        mocked_get_sender_id = mocker.patch("app.celery.tasks.dao_get_service_sms_senders_by_id", return_value=sms_sender)
        celery_task = "deliver_throttled_sms" if sender_id else "deliver_sms"
        mocked_deliver_sms = mocker.patch(f"app.celery.provider_tasks.{celery_task}.apply_async")
        json_template_date = {"data": template_schema.dump(sample_template_with_placeholders).data}
        json_service_data = {"data": service_schema.dump(sample_service).data}
        mocked_redis_get = mocker.patch.object(redis_store, "get")

        mocked_redis_get.side_effect = [
            bytes(json.dumps(json_service_data, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"),
            bytes(
                json.dumps(json_template_date, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"
            ),
            bytes(
                json.dumps(json_template_date, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"
            ),
            bytes(json.dumps(json_service_data, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"),
            False,
            False,
            False,
        ]
        mocker.patch("app.notifications.process_notifications.choose_queue", return_value="sms_queue")
        save_smss(sample_template_with_placeholders.service_id, [signer.sign(notification)], uuid.uuid4())

        assert mocked_redis_get.called
        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "+1 650 253 2222"
        assert persisted_notification.template_id == sample_template_with_placeholders.id
        assert persisted_notification.template_version == sample_template_with_placeholders.version
        assert persisted_notification.status == "created"
        assert persisted_notification.created_at <= datetime.utcnow()
        assert not persisted_notification.sent_at
        assert not persisted_notification.sent_by
        assert not persisted_notification.job_id
        assert persisted_notification.personalisation == {"name": "Jo"}
        assert persisted_notification._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification.notification_type == "sms"
        mocked_deliver_sms.assert_called_once_with(
            [str(persisted_notification.id)], queue="send-throttled-sms-tasks" if sender_id else "send-sms-tasks"
        )
        if sender_id:
            mocked_get_sender_id.assert_called_once_with(persisted_notification.service_id, sender_id)

    def test_should_put_save_sms_task_in_research_mode_queue_if_research_mode_service(self, notify_db, notify_db_session, mocker):
        service = create_service(
            research_mode=True,
        )

        template = create_template(service=service)

        notification = _notification_json(template, to="+1 650 253 2222")

        mocked_deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        notification_id = uuid.uuid4()

        save_smss(template.service_id, [signer.sign(notification)], notification_id)
        persisted_notification = Notification.query.one()
        provider_tasks.deliver_sms.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="research-mode-tasks"
        )
        assert mocked_deliver_sms.called

    @pytest.mark.parametrize("process_type", ["priority", "bulk"])
    def test_should_route_save_sms_task_to_appropriate_queue_according_to_template_process_type(
        self, notify_db, notify_db_session, mocker, process_type
    ):
        service = create_service()
        template = create_template(service=service, process_type=process_type)
        notification = _notification_json(template, to="+1 650 253 2222")

        mocked_deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        notification_id = uuid.uuid4()

        save_smss(
            template.service_id,
            [signer.sign(notification)],
            notification_id,
        )
        persisted_notification = Notification.query.one()
        provider_tasks.deliver_sms.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue=f"{process_type}-tasks"
        )
        assert mocked_deliver_sms.called

    def test_should_route_save_sms_task_to_bulk_on_large_csv_file(self, notify_db, notify_db_session, mocker):
        service = create_service()
        template = create_template(service=service, process_type="normal")
        notification = _notification_json(template, to="+1 650 253 2222", queue="bulk-tasks")

        mocked_deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        notification_id = uuid.uuid4()

        save_smss(
            template.service_id,
            [signer.sign(notification)],
            notification_id,
        )
        persisted_notification = Notification.query.one()
        provider_tasks.deliver_sms.apply_async.assert_called_once_with([str(persisted_notification.id)], queue="bulk-tasks")
        assert mocked_deliver_sms.called

    def test_should_route_save_sms_task_to_throttled_queue_on_large_csv_file_if_custom_sms_sender(
        self, notify_db, notify_db_session, mocker
    ):
        service = create_service_with_defined_sms_sender(sms_sender_value="3433061234")
        template = create_template(service=service, process_type="normal")
        notification = _notification_json(template, to="+1 650 253 2222", queue="bulk-tasks")

        mocked_deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_throttled_sms.apply_async")
        mocked_deliver_throttled_sms = mocker.patch("app.celery.provider_tasks.deliver_throttled_sms.apply_async")

        notification_id = uuid.uuid4()

        save_smss(template.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        provider_tasks.deliver_throttled_sms.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="send-throttled-sms-tasks"
        )
        mocked_deliver_sms.assert_not_called()
        mocked_deliver_throttled_sms.assert_called_once()

    def test_should_save_sms_if_restricted_service_and_valid_number(self, notify_db_session, mocker):
        user = create_user(mobile_number="6502532222")
        service = create_service(user=user, restricted=True)
        template = create_template(service=service)
        notification = _notification_json(template, "+16502532222")

        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        notification_id = uuid.uuid4()
        save_smss(template.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "+16502532222"
        assert persisted_notification.template_id == template.id
        assert persisted_notification.template_version == template.version
        assert persisted_notification.status == "created"
        assert persisted_notification.created_at <= datetime.utcnow()
        assert not persisted_notification.sent_at
        assert not persisted_notification.sent_by
        assert not persisted_notification.job_id
        assert not persisted_notification.personalisation
        assert persisted_notification.notification_type == "sms"
        provider_tasks.deliver_sms.apply_async.assert_called_once_with([str(persisted_notification.id)], queue="send-sms-tasks")

    def test_save_sms_should_save_default_smm_sender_notification_reply_to_text_on(self, notify_db_session, mocker):
        service = create_service_with_defined_sms_sender(sms_sender_value="12345")
        template = create_template(service=service)

        notification = _notification_json(template, to="6502532222")
        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        notification_id = uuid.uuid4()
        save_smss(template.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.reply_to_text == "12345"

    def test_should_save_sms_template_to_and_persist_with_job_id(self, sample_job, mocker):
        notification = _notification_json(sample_job.template, to="+1 650 253 2222", job_id=sample_job.id, row_number=2)
        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")
        mock_over_daily_limit = mocker.patch("app.celery.tasks.check_service_over_daily_message_limit")

        notification_id = uuid.uuid4()
        now = datetime.utcnow()
        save_smss(sample_job.template.service_id, [signer.sign(notification)], notification_id)
        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "+1 650 253 2222"
        assert persisted_notification.job_id == sample_job.id
        assert persisted_notification.template_id == sample_job.template.id
        assert persisted_notification.status == "created"
        assert not persisted_notification.sent_at
        assert persisted_notification.created_at >= now
        assert not persisted_notification.sent_by
        assert persisted_notification.job_row_number == 2
        assert persisted_notification.api_key_id is None
        assert persisted_notification.key_type == KEY_TYPE_NORMAL
        assert persisted_notification.notification_type == "sms"

        provider_tasks.deliver_sms.apply_async.assert_called_once_with([str(persisted_notification.id)], queue="send-sms-tasks")
        mock_over_daily_limit.assert_called_once_with("normal", sample_job.service)

    def test_save_sms_should_go_to_retry_queue_if_database_errors(self, sample_template, mocker):
        notification = _notification_json(sample_template, "+1 650 253 2222")

        expected_exception = SQLAlchemyError()

        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")
        mocker.patch("app.celery.tasks.save_smss.retry", side_effect=Retry)
        mocker.patch("app.celery.tasks.save_smss.max_retries", return_value=4)
        mocker.patch(
            "app.notifications.process_notifications.bulk_insert_notifications",
            side_effect=expected_exception,
        )

        notification_id = uuid.uuid4()

        with pytest.raises(Retry):
            save_smss(sample_template.service_id, [signer.sign(notification)], notification_id)
        assert provider_tasks.deliver_sms.apply_async.called is False
        tasks.save_smss.retry.assert_called_with(exc=expected_exception, queue="retry-tasks")

        assert Notification.query.count() == 0

    def test_save_sms_does_not_send_duplicate_and_does_not_put_in_retry_queue(self, sample_notification, mocker):
        json = _notification_json(sample_notification.template, "6502532222", job_id=uuid.uuid4(), row_number=1)
        deliver_sms = mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")
        retry = mocker.patch("app.celery.tasks.save_smss.retry", side_effect=Exception())
        notification_id = str(sample_notification.id)
        json["id"] = str(sample_notification.id)

        save_smss(sample_notification.service_id, [signer.sign(json)], notification_id)
        assert Notification.query.count() == 1
        assert not deliver_sms.called
        assert not retry.called

    def test_save_sms_uses_sms_sender_reply_to_text(self, mocker, notify_db_session):
        service = create_service_with_defined_sms_sender(sms_sender_value="6502532222")
        template = create_template(service=service)

        notification = _notification_json(template, to="6502532222")
        mocker.patch("app.celery.provider_tasks.deliver_throttled_sms.apply_async")

        notification_id = uuid.uuid4()
        save_smss(service.id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.reply_to_text == "+16502532222"

    def test_save_sms_uses_non_default_sms_sender_reply_to_text_if_provided(self, mocker, notify_db_session):
        service = create_service_with_defined_sms_sender(sms_sender_value="07123123123")
        template = create_template(service=service)
        new_sender = service_sms_sender_dao.dao_add_sms_sender_for_service(service.id, "new-sender", False)

        notification = _notification_json(template, to="6502532222")
        notification["sender_id"] = str(new_sender.id)
        mocker.patch("app.celery.provider_tasks.deliver_sms.apply_async")

        notification_id = uuid.uuid4()
        save_smss(service.id, [signer.sign(notification)], notification_id)
        persisted_notification = Notification.query.one()
        assert persisted_notification.reply_to_text == "new-sender"


class TestSaveErrorHandling:
    def test_handler_send_1notification(self, sample_template, mocker):
        n1 = _notification_json(sample_template, "+1 650 253 2222")
        n1["notification_id"] = str(uuid.uuid4())
        service = dao_fetch_service_by_id(sample_template.service_id)
        n1["service"] = service
        n1["template_id"] = str(sample_template.id)
        expected_exception = SQLAlchemyError()

        retry_func = mocker.patch("app.celery.tasks.save_smss.retry")
        mocker.patch("app.celery.tasks.save_smss.apply_async", side_effect=handle_batch_error_and_forward)
        mocker.patch(
            "app.notifications.process_notifications.bulk_insert_notifications",
            side_effect=expected_exception,
        )

        receipt_id = uuid.uuid4()

        signed_notifications = [1]
        verified_notifications = [n1]
        signed_and_verified = list(zip(signed_notifications, verified_notifications))
        handle_batch_error_and_forward(save_smss, signed_and_verified, SMS_TYPE, expected_exception, receipt_id, sample_template)
        retry_func.assert_called_with(exc=expected_exception, queue="retry-tasks")

    def test_handler_send_3notifications(self, sample_template, mocker):
        n1 = _notification_json(sample_template, "+1 650 253 2222")
        n2 = _notification_json(sample_template, "+1 234 456 7890")
        n3 = _notification_json(sample_template, "+1 345 567 7890")
        n1["notification_id"] = str(uuid.uuid4())
        n2["notification_id"] = str(uuid.uuid4())
        n3["notification_id"] = str(uuid.uuid4())
        service = dao_fetch_service_by_id(sample_template.service_id)
        n1["service"] = service
        n2["service"] = service
        n3["service"] = service
        n1["template_id"] = str(sample_template.id)
        n2["template_id"] = str(sample_template.id)
        n3["template_id"] = str(sample_template.id)
        expected_exception = SQLAlchemyError()

        save_func = mocker.patch("app.celery.tasks.save_smss.apply_async")

        receipt_id = uuid.uuid4()

        signed_notifications = [1, 2, 3]
        verified_notifications = [n1, n2, n3]
        signed_and_verified = list(zip(signed_notifications, verified_notifications))
        handle_batch_error_and_forward(save_smss, signed_and_verified, SMS_TYPE, expected_exception, receipt_id, sample_template)

        assert save_func.call_count == 3
        assert save_func.call_args_list == [
            call((service.id, [1], None), queue="-normal-database-tasks"),
            call((service.id, [2], None), queue="-normal-database-tasks"),
            call((service.id, [3], None), queue="-normal-database-tasks"),
        ]

    def test_should_forward_sms_on_error(self, sample_template_with_placeholders, mocker):
        notification1 = _notification_json(
            sample_template_with_placeholders,
            to="+1 650 253 2221",
            personalisation={"name": "Jo"},
        )
        notification1["id"] = str(uuid.uuid4())
        notification1["service_id"] = str(sample_template_with_placeholders.service.id)
        expected_error = IntegrityError(None, None, None)
        mock_persist_notifications = mocker.patch("app.celery.tasks.persist_notifications", side_effect=expected_error)
        mock_save_sms = mocker.patch("app.celery.tasks.save_smss.retry")
        mock_acknowldege = mocker.patch("app.sms_normal.acknowledge")

        receipt = uuid.uuid4()
        notifications = [signer.sign(notification1)]

        save_smss(
            str(sample_template_with_placeholders.service.id),
            notifications,
            receipt,
        )

        mock_persist_notifications.assert_called_once()
        mock_save_sms.assert_called_with(queue="retry-tasks", exc=expected_error)
        mock_acknowldege.assert_called_once_with(receipt)

    def test_should_forward_email_on_error(self, sample_email_template_with_placeholders, mocker):
        notification1 = _notification_json(
            sample_email_template_with_placeholders,
            to="test1@gmail.com",
            personalisation={"name": "Jo"},
        )
        notification1["id"] = str(uuid.uuid4())
        notification1["service_id"] = str(sample_email_template_with_placeholders.service.id)

        expected_error = IntegrityError(None, None, None)
        mock_persist_notifications = mocker.patch("app.celery.tasks.persist_notifications", side_effect=expected_error)
        mock_save_email = mocker.patch("app.celery.tasks.save_emails.retry")
        mock_acknowldege = mocker.patch("app.email_normal.acknowledge")

        receipt = uuid.uuid4()
        notifications = [signer.sign(notification1)]

        save_emails(
            str(sample_email_template_with_placeholders.service.id),
            notifications,
            receipt,
        )

        mock_persist_notifications.assert_called_once()
        mock_save_email.assert_called_with(queue="retry-tasks", exc=expected_error)
        mock_acknowldege.assert_called_once_with(receipt)


class TestSaveEmails:
    @pytest.mark.parametrize("sender_id", [None, "996958a8-0c06-43be-a40e-56e4a2d1655c"])
    def test_save_emails_should_use_redis_cache_to_retrieve_service_and_template_when_possible(
        self, sample_service, mocker, sender_id
    ):
        sample_template = create_template(
            template_name="Test Template",
            template_type="email",
            content="Hello (( Name))\nYour thing is due soon",
            service=sample_service,
        )

        notification = _notification_json(
            sample_template,
            to="test@unittest.com",
            personalisation={"name": "Jo"},
        )

        if sender_id:
            notification["sender_id"] = sender_id

        reply_to = ServiceEmailReplyTo()
        reply_to.email_address = "notify@digital.cabinet-office.gov.uk"
        mocked_get_sender_id = mocker.patch("app.celery.tasks.dao_get_reply_to_by_id", return_value=reply_to)
        mocked_deliver_email = mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        json_template_date = {"data": template_schema.dump(sample_template).data}
        json_service_data = {"data": service_schema.dump(sample_service).data}
        mocked_redis_get = mocker.patch.object(redis_store, "get")

        mocked_redis_get.side_effect = [
            bytes(json.dumps(json_service_data, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"),
            bytes(
                json.dumps(json_template_date, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"
            ),
            bytes(
                json.dumps(json_template_date, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"
            ),
            bytes(json.dumps(json_service_data, default=lambda o: o.hex if isinstance(o, uuid.UUID) else None), encoding="utf-8"),
            False,
            False,
        ]
        mocker.patch("app.notifications.process_notifications.choose_queue", return_value="email_normal_queue")

        save_emails(sample_template.service_id, [signer.sign(notification)], uuid.uuid4())
        assert mocked_redis_get.called
        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "test@unittest.com"
        assert persisted_notification.template_id == sample_template.id
        assert persisted_notification.template_version == sample_template.version
        assert persisted_notification.status == "created"
        assert persisted_notification.created_at <= datetime.utcnow()
        assert not persisted_notification.sent_at
        assert not persisted_notification.sent_by
        assert not persisted_notification.job_id
        assert persisted_notification.personalisation == {"name": "Jo"}
        assert persisted_notification._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification.notification_type == "email"
        mocked_deliver_email.assert_called_once_with([str(persisted_notification.id)], queue="send-email-tasks")
        if sender_id:
            mocked_get_sender_id.assert_called_once_with(persisted_notification.service_id, sender_id)

    def test_save_email_should_save_default_email_reply_to_text_on_notification(self, notify_db_session, mocker):
        service = create_service()
        create_reply_to_email(service=service, email_address="reply_to@digital.gov.uk", is_default=True)
        template = create_template(service=service, template_type="email", subject="Hello")

        notification = _notification_json(template, to="test@example.com")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()
        save_emails(service.id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.reply_to_text == "reply_to@digital.gov.uk"

    def test_save_email_should_save_non_default_email_reply_to_text_on_notification_when_set(self, notify_db_session, mocker):
        service = create_service()
        create_reply_to_email(service=service, email_address="reply_to@digital.gov.uk", is_default=True)
        create_reply_to_email(service=service, email_address="reply_two@digital.gov.uk", is_default=False)
        template = create_template(service=service, template_type="email", subject="Hello")

        notification = _notification_json(template, to="test@example.com", reply_to_text="reply_two@digital.gov.uk")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()
        save_emails(service.id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.reply_to_text == "reply_two@digital.gov.uk"

    def test_should_put_save_email_task_in_research_mode_queue_if_research_mode_service(self, notify_db_session, mocker):
        service = create_service(research_mode=True)

        template = create_template(service=service, template_type="email")

        notification = _notification_json(template, to="test@test.com")

        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()

        save_emails(service.id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        provider_tasks.deliver_email.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="research-mode-tasks"
        )

    @pytest.mark.parametrize("process_type", ["priority", "bulk"])
    def test_should_route_save_email_task_to_appropriate_queue_according_to_template_process_type(
        self, notify_db_session, mocker, process_type
    ):
        service = create_service()
        template = create_template(service=service, template_type="email", process_type=process_type)
        notification = _notification_json(template, to="test@test.com")

        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()

        save_emails(service.id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        provider_tasks.deliver_email.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue=f"{process_type}-tasks"
        )

    def test_should_route_save_email_task_to_bulk_on_large_csv_file(self, notify_db_session, mocker):
        service = create_service()
        template = create_template(service=service, template_type="email", process_type="normal")
        notification = _notification_json(template, to="test@test.com", queue="bulk-tasks")

        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()

        save_emails(service.id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        provider_tasks.deliver_email.apply_async.assert_called_once_with([str(persisted_notification.id)], queue="bulk-tasks")

    def test_should_use_email_template_and_persist(self, sample_email_template_with_placeholders, sample_api_key, mocker):
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        mock_over_daily_limit = mocker.patch("app.celery.tasks.check_service_over_daily_message_limit")

        now = datetime(2016, 1, 1, 11, 9, 0)
        notification_id = uuid.uuid4()

        with freeze_time("2016-01-01 12:00:00.000000"):
            notification = _notification_json(
                sample_email_template_with_placeholders,
                "my_email@my_email.com",
                {"name": "Jo"},
                row_number=1,
            )

        with freeze_time("2016-01-01 11:10:00.00000"):
            save_emails(sample_email_template_with_placeholders.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "my_email@my_email.com"
        assert persisted_notification.template_id == sample_email_template_with_placeholders.id
        assert persisted_notification.template_version == sample_email_template_with_placeholders.version
        assert persisted_notification.created_at >= now
        assert not persisted_notification.sent_at
        assert persisted_notification.status == "created"
        assert not persisted_notification.sent_by
        assert persisted_notification.job_row_number == 1
        assert persisted_notification.personalisation == {"name": "Jo"}
        assert persisted_notification._personalisation == signer.sign({"name": "Jo"})
        assert persisted_notification.api_key_id is None
        assert persisted_notification.key_type == KEY_TYPE_NORMAL
        assert persisted_notification.notification_type == "email"

        provider_tasks.deliver_email.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="send-email-tasks"
        )
        mock_over_daily_limit.assert_called_once_with("normal", sample_email_template_with_placeholders.service)

    def test_save_email_should_use_template_version_from_job_not_latest(self, sample_email_template, mocker):
        notification = _notification_json(sample_email_template, "my_email@my_email.com")
        version_on_notification = sample_email_template.version
        # Change the template
        from app.dao.templates_dao import dao_get_template_by_id, dao_update_template

        sample_email_template.content = sample_email_template.content + " another version of the template"
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        dao_update_template(sample_email_template)
        t = dao_get_template_by_id(sample_email_template.id)
        assert t.version > version_on_notification
        now = datetime.utcnow()

        save_emails(sample_email_template.service_id, [signer.sign(notification)], uuid.uuid4())

        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "my_email@my_email.com"
        assert persisted_notification.template_id == sample_email_template.id
        assert persisted_notification.template_version == version_on_notification
        assert persisted_notification.created_at >= now
        assert not persisted_notification.sent_at
        assert persisted_notification.status == "created"
        assert not persisted_notification.sent_by
        assert persisted_notification.notification_type == "email"
        provider_tasks.deliver_email.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="send-email-tasks"
        )

    def test_should_use_email_template_subject_placeholders(self, sample_email_template_with_placeholders, mocker):
        notification = _notification_json(sample_email_template_with_placeholders, "my_email@my_email.com", {"name": "Jo"})
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()
        now = datetime.utcnow()

        save_emails(sample_email_template_with_placeholders.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "my_email@my_email.com"
        assert persisted_notification.template_id == sample_email_template_with_placeholders.id
        assert persisted_notification.status == "created"
        assert persisted_notification.created_at >= now
        assert not persisted_notification.sent_by
        assert persisted_notification.personalisation == {"name": "Jo"}
        assert not persisted_notification.reference
        assert persisted_notification.notification_type == "email"
        provider_tasks.deliver_email.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="send-email-tasks"
        )

    def test_save_email_uses_the_reply_to_text_when_provided(self, sample_email_template, mocker):
        notification = _notification_json(sample_email_template, "my_email@my_email.com")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        service = sample_email_template.service
        notification_id = uuid.uuid4()
        service_email_reply_to_dao.add_reply_to_email_address_for_service(service.id, "default@example.com", True)
        other_email_reply_to = service_email_reply_to_dao.add_reply_to_email_address_for_service(
            service.id, "other@example.com", False
        )

        notification["sender_id"] = str(other_email_reply_to.id)

        save_emails(sample_email_template.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.notification_type == "email"
        assert persisted_notification.reply_to_text == "other@example.com"

    def test_save_email_uses_the_default_reply_to_text_if_sender_id_is_none(self, sample_email_template, mocker):
        notification = _notification_json(sample_email_template, "my_email@my_email.com")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        service = sample_email_template.service
        notification_id = uuid.uuid4()
        service_email_reply_to_dao.add_reply_to_email_address_for_service(service.id, "default@example.com", True)

        save_emails(sample_email_template.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.notification_type == "email"
        assert persisted_notification.reply_to_text == "default@example.com"

    def test_should_use_email_template_and_persist_without_personalisation(self, sample_email_template, mocker):
        notification = _notification_json(sample_email_template, "my_email@my_email.com")
        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")

        notification_id = uuid.uuid4()

        now = datetime.utcnow()

        save_emails(sample_email_template.service_id, [signer.sign(notification)], notification_id)

        persisted_notification = Notification.query.one()
        assert persisted_notification.to == "my_email@my_email.com"
        assert persisted_notification.template_id == sample_email_template.id
        assert persisted_notification.created_at >= now
        assert not persisted_notification.sent_at
        assert persisted_notification.status == "created"
        assert not persisted_notification.sent_by
        assert not persisted_notification.personalisation
        assert not persisted_notification.reference
        assert persisted_notification.notification_type == "email"
        provider_tasks.deliver_email.apply_async.assert_called_once_with(
            [str(persisted_notification.id)], queue="send-email-tasks"
        )

    def test_save_email_should_go_to_retry_queue_if_database_errors(self, sample_email_template, mocker):
        notification = _notification_json(sample_email_template, "test@example.gov.uk")

        expected_exception = SQLAlchemyError()

        mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        mocker.patch("app.celery.tasks.save_emails.retry", side_effect=Retry)
        mocker.patch("app.celery.tasks.save_emails.max_retries", return_value=4)
        mocker.patch(
            "app.notifications.process_notifications.bulk_insert_notifications",
            side_effect=expected_exception,
        )
        notification_id = uuid.uuid4()

        with pytest.raises(Retry):

            save_emails(sample_email_template.service_id, [signer.sign(notification)], notification_id)

        assert not provider_tasks.deliver_email.apply_async.called
        tasks.save_emails.retry.assert_called_with(exc=expected_exception, queue="retry-tasks")

        assert Notification.query.count() == 0

    def test_save_email_does_not_send_duplicate_and_does_not_put_in_retry_queue(self, sample_notification, mocker):
        json = _notification_json(
            sample_notification.template,
            sample_notification.to,
            job_id=uuid.uuid4(),
            row_number=1,
        )
        deliver_email = mocker.patch("app.celery.provider_tasks.deliver_email.apply_async")
        retry = mocker.patch("app.celery.tasks.save_emails.retry", side_effect=Exception())
        notification_id = str(sample_notification.id)
        json["id"] = str(sample_notification.id)

        save_emails(sample_notification.service_id, [signer.sign(json)], notification_id)
        assert Notification.query.count() == 1
        assert not deliver_email.called
        assert not retry.called


@pytest.mark.parametrize(
    "template_type, expected_class",
    [
        (SMS_TYPE, SMSMessageTemplate),
        (EMAIL_TYPE, WithSubjectTemplate),
        (LETTER_TYPE, WithSubjectTemplate),
    ],
)
def test_get_template_class(template_type, expected_class):
    assert get_template_class(template_type) == expected_class


class TestSendInboundSmsToService:
    def test_send_inbound_sms_to_service_post_https_request_to_service(self, notify_api, sample_service):
        inbound_api = create_service_inbound_api(
            service=sample_service,
            url="https://some.service.gov.uk/",
            bearer_token="something_unique",
        )
        inbound_sms = create_inbound_sms(
            service=sample_service,
            notify_number="0751421",
            user_number="447700900111",
            provider_date=datetime(2017, 6, 20),
            content="Here is some content",
        )
        data = {
            "id": str(inbound_sms.id),
            "source_number": inbound_sms.user_number,
            "destination_number": inbound_sms.notify_number,
            "message": inbound_sms.content,
            "date_received": inbound_sms.provider_date.strftime(DATETIME_FORMAT),
        }

        with requests_mock.Mocker() as request_mock:
            request_mock.post(inbound_api.url, json={}, status_code=200)
            send_inbound_sms_to_service(inbound_sms.id, inbound_sms.service_id)
        assert request_mock.call_count == 1
        assert request_mock.request_history[0].url == inbound_api.url
        assert request_mock.request_history[0].method == "POST"
        assert request_mock.request_history[0].text == json.dumps(data)
        assert request_mock.request_history[0].headers["Content-type"] == "application/json"
        assert request_mock.request_history[0].headers["Authorization"] == "Bearer {}".format(inbound_api.bearer_token)

    def test_send_inbound_sms_to_service_does_not_send_request_when_inbound_sms_does_not_exist(self, notify_api, sample_service):
        inbound_api = create_service_inbound_api(service=sample_service)
        with requests_mock.Mocker() as request_mock:
            request_mock.post(inbound_api.url, json={}, status_code=200)
            with pytest.raises(SQLAlchemyError):
                send_inbound_sms_to_service(inbound_sms_id=uuid.uuid4(), service_id=sample_service.id)

        assert request_mock.call_count == 0

    def test_send_inbound_sms_to_service_does_not_sent_request_when_inbound_api_does_not_exist(
        self, notify_api, sample_service, mocker
    ):
        inbound_sms = create_inbound_sms(
            service=sample_service,
            notify_number="0751421",
            user_number="447700900111",
            provider_date=datetime(2017, 6, 20),
            content="Here is some content",
        )
        mocked = mocker.patch("requests.request")
        send_inbound_sms_to_service(inbound_sms.id, inbound_sms.service_id)

        mocked.call_count == 0

    def test_send_inbound_sms_to_service_retries_if_request_returns_500(self, notify_api, sample_service, mocker):
        inbound_api = create_service_inbound_api(
            service=sample_service,
            url="https://some.service.gov.uk/",
            bearer_token="something_unique",
        )
        inbound_sms = create_inbound_sms(
            service=sample_service,
            notify_number="0751421",
            user_number="447700900111",
            provider_date=datetime(2017, 6, 20),
            content="Here is some content",
        )

        mocked = mocker.patch("app.celery.tasks.send_inbound_sms_to_service.retry")
        with requests_mock.Mocker() as request_mock:
            request_mock.post(inbound_api.url, json={}, status_code=500)
            send_inbound_sms_to_service(inbound_sms.id, inbound_sms.service_id)

        assert mocked.call_count == 1
        assert mocked.call_args[1]["queue"] == "retry-tasks"

    def test_send_inbound_sms_to_service_retries_if_request_throws_unknown(self, notify_api, sample_service, mocker):
        create_service_inbound_api(
            service=sample_service,
            url="https://some.service.gov.uk/",
            bearer_token="something_unique",
        )
        inbound_sms = create_inbound_sms(
            service=sample_service,
            notify_number="0751421",
            user_number="447700900111",
            provider_date=datetime(2017, 6, 20),
            content="Here is some content",
        )

        mocked = mocker.patch("app.celery.tasks.send_inbound_sms_to_service.retry")
        mocker.patch("app.celery.tasks.request", side_effect=RequestException())

        send_inbound_sms_to_service(inbound_sms.id, inbound_sms.service_id)

        assert mocked.call_count == 1
        assert mocked.call_args[1]["queue"] == "retry-tasks"

    def test_send_inbound_sms_to_service_does_not_retries_if_request_returns_404(self, notify_api, sample_service, mocker):
        inbound_api = create_service_inbound_api(
            service=sample_service,
            url="https://some.service.gov.uk/",
            bearer_token="something_unique",
        )
        inbound_sms = create_inbound_sms(
            service=sample_service,
            notify_number="0751421",
            user_number="447700900111",
            provider_date=datetime(2017, 6, 20),
            content="Here is some content",
        )

        mocked = mocker.patch("app.celery.tasks.send_inbound_sms_to_service.retry")
        with requests_mock.Mocker() as request_mock:
            request_mock.post(inbound_api.url, json={}, status_code=404)
            send_inbound_sms_to_service(inbound_sms.id, inbound_sms.service_id)

        mocked.call_count == 0


class TestProcessIncompleteJob:
    def test_process_incomplete_job_sms(self, mocker, sample_template):
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        save_smss = mocker.patch("app.celery.tasks.save_smss.apply_async")

        job = create_job(
            template=sample_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        save_notification(create_notification(sample_template, job, 0))
        save_notification(create_notification(sample_template, job, 1))

        assert Notification.query.filter(Notification.job_id == job.id).count() == 2

        process_incomplete_job(str(job.id))

        completed_job = Job.query.filter(Job.id == job.id).one()

        assert completed_job.job_status == JOB_STATUS_FINISHED

        assert save_smss.call_count == 1  # The save_smss call will be called once
        assert len(save_smss.call_args[0][0][1]) == 8  # The unprocessed 8 notifications will be sent to save_smss

    def test_process_incomplete_job_with_notifications_all_sent(self, mocker, sample_template):

        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mock_save_sms = mocker.patch("app.celery.tasks.save_smss.apply_async")

        job = create_job(
            template=sample_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        save_notification(create_notification(sample_template, job, 0))
        save_notification(create_notification(sample_template, job, 1))
        save_notification(create_notification(sample_template, job, 2))
        save_notification(create_notification(sample_template, job, 3))
        save_notification(create_notification(sample_template, job, 4))
        save_notification(create_notification(sample_template, job, 5))
        save_notification(create_notification(sample_template, job, 6))
        save_notification(create_notification(sample_template, job, 7))
        save_notification(create_notification(sample_template, job, 8))
        save_notification(create_notification(sample_template, job, 9))

        assert Notification.query.filter(Notification.job_id == job.id).count() == 10

        process_incomplete_job(str(job.id))

        completed_job = Job.query.filter(Job.id == job.id).one()

        assert completed_job.job_status == JOB_STATUS_FINISHED

        assert mock_save_sms.call_count == 0  # There are 10 in the file and we've added 10 it should not have been called

    def test_process_incomplete_jobs_sms(self, mocker, sample_template):

        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mock_save_smss = mocker.patch("app.celery.tasks.save_smss.apply_async")

        job = create_job(
            template=sample_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )
        save_notification(create_notification(sample_template, job, 0))
        save_notification(create_notification(sample_template, job, 1))
        save_notification(create_notification(sample_template, job, 2))

        assert Notification.query.filter(Notification.job_id == job.id).count() == 3

        job2 = create_job(
            template=sample_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        save_notification(create_notification(sample_template, job2, 0))
        save_notification(create_notification(sample_template, job2, 1))
        save_notification(create_notification(sample_template, job2, 2))
        save_notification(create_notification(sample_template, job2, 3))
        save_notification(create_notification(sample_template, job2, 4))

        assert Notification.query.filter(Notification.job_id == job2.id).count() == 5

        jobs = [job.id, job2.id]
        process_incomplete_jobs(jobs)

        completed_job = Job.query.filter(Job.id == job.id).one()
        completed_job2 = Job.query.filter(Job.id == job2.id).one()

        assert completed_job.job_status == JOB_STATUS_FINISHED

        assert completed_job2.job_status == JOB_STATUS_FINISHED

        assert mock_save_smss.call_count == 2
        # The second time the job is called we will send 5 notifications through
        assert len(mock_save_smss.call_args[0][0][1]) == 5

    def test_process_incomplete_jobs_no_notifications_added(self, mocker, sample_template):
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mock_save_sms = mocker.patch("app.celery.tasks.save_smss.apply_async")

        job = create_job(
            template=sample_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        assert Notification.query.filter(Notification.job_id == job.id).count() == 0

        process_incomplete_job(job.id)

        completed_job = Job.query.filter(Job.id == job.id).one()

        assert completed_job.job_status == JOB_STATUS_FINISHED

        assert mock_save_sms.call_count == 1
        assert len(mock_save_sms.call_args[0][0][1]) == 10  # There are 10 in the csv file

    def test_process_incomplete_jobs(self, mocker):

        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mock_save_sms = mocker.patch("app.celery.tasks.save_smss.apply_async")

        jobs = []
        process_incomplete_jobs(jobs)

        assert mock_save_sms.call_count == 0  # There are no jobs to process so it will not have been called

    def test_process_incomplete_job_no_job_in_database(self, mocker, fake_uuid):

        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_sms"),
        )
        mock_save_sms = mocker.patch("app.celery.tasks.save_smss.apply_async")

        with pytest.raises(expected_exception=Exception):
            process_incomplete_job(fake_uuid)

        assert mock_save_sms.call_count == 0  # There is no job in the db it will not have been called

    def test_process_incomplete_job_email(self, mocker, sample_email_template):

        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_email"),
        )
        mock_email_saver = mocker.patch("app.celery.tasks.save_emails.apply_async")

        job = create_job(
            template=sample_email_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        save_notification(create_notification(sample_email_template, job, 0))
        save_notification(create_notification(sample_email_template, job, 1))

        assert Notification.query.filter(Notification.job_id == job.id).count() == 2

        process_incomplete_job(str(job.id))

        completed_job = Job.query.filter(Job.id == job.id).one()

        assert completed_job.job_status == JOB_STATUS_FINISHED

        assert mock_email_saver.call_count == 1
        assert len(mock_email_saver.call_args[0][0][1]) == 8  # There are 10 in the file and we've added two already

    @pytest.mark.skip(reason="DEPRECATED: letter code")
    def test_process_incomplete_job_letter(self, mocker, sample_letter_template):
        mocker.patch(
            "app.celery.tasks.s3.get_job_from_s3",
            return_value=load_example_csv("multiple_letter"),
        )
        mock_letter_saver = mocker.patch("app.celery.tasks.save_letter.apply_async")

        job = create_job(
            template=sample_letter_template,
            notification_count=10,
            created_at=datetime.utcnow() - timedelta(hours=2),
            scheduled_for=datetime.utcnow() - timedelta(minutes=31),
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        save_notification(create_notification(sample_letter_template, job, 0))
        save_notification(create_notification(sample_letter_template, job, 1))

        assert Notification.query.filter(Notification.job_id == job.id).count() == 2

        process_incomplete_job(str(job.id))

        assert mock_letter_saver.call_count == 8

    @freeze_time("2017-01-01")
    def test_process_incomplete_jobs_sets_status_to_in_progress_and_resets_processing_started_time(self, mocker, sample_template):
        mock_process_incomplete_job = mocker.patch("app.celery.tasks.process_incomplete_job")

        job1 = create_job(
            sample_template,
            processing_started=datetime.utcnow() - timedelta(minutes=30),
            job_status=JOB_STATUS_ERROR,
        )
        job2 = create_job(
            sample_template,
            processing_started=datetime.utcnow() - timedelta(minutes=31),
            job_status=JOB_STATUS_ERROR,
        )

        process_incomplete_jobs([str(job1.id), str(job2.id)])

        assert job1.job_status == JOB_STATUS_IN_PROGRESS
        assert job1.processing_started == datetime.utcnow()

        assert job2.job_status == JOB_STATUS_IN_PROGRESS
        assert job2.processing_started == datetime.utcnow()

        assert mock_process_incomplete_job.mock_calls == [
            call(str(job1.id)),
            call(str(job2.id)),
        ]


class TestSendNotifyNoReply:
    def test_send_notify_no_reply(self, mocker, sample_notification, no_reply_template):
        persist_mock = mocker.patch("app.celery.tasks.persist_notifications", return_value=[sample_notification])
        queue_mock = mocker.patch("app.celery.tasks.send_notification_to_queue")

        data = json.dumps(
            {
                "sender": "sender@example.com",
                "recipients": ["service@notify.ca"],
            }
        )

        send_notify_no_reply(data)

        assert len(persist_mock.call_args_list) == 1
        persist_call = persist_mock.call_args_list[0][0][0][0]
        assert persist_call["recipient"] == "sender@example.com"
        assert persist_call["personalisation"] == {
            "sending_email_address": "service@notify.ca",
        }
        assert persist_call["reply_to_text"] is None
        assert len(queue_mock.call_args_list) == 1
        queue_call = queue_mock.call_args_list[0][1]

        assert queue_call["queue"] == QueueNames.NOTIFY

    def test_send_notify_no_reply_retry(self, mocker, no_reply_template):
        mocker.patch("app.celery.tasks.send_notify_no_reply.retry", side_effect=Retry)
        mocker.patch("app.celery.tasks.send_notification_to_queue", side_effect=Exception())

        with pytest.raises(Retry):
            send_notify_no_reply(
                json.dumps(
                    {
                        "sender": "sender@example.com",
                        "recipients": ["service@notify.ca"],
                    }
                )
            )

        tasks.send_notify_no_reply.retry.assert_called_with(queue=QueueNames.RETRY)
