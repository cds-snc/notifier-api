import uuid
from datetime import datetime
from unittest.mock import ANY, call

import pytest
import requests_mock
from flask import current_app, json
from freezegun import freeze_time
from tests.conftest import Matcher, set_config_values

from app.aws.mocks import (
    pinpoint_delivered_callback,
    pinpoint_failed_callback,
    ses_notification_callback,
    sns_failed_callback,
    sns_success_callback,
)
from app.celery.research_mode_tasks import (
    create_fake_letter_response_file,
    send_email_response,
    send_sms_response,
)
from app.config import QueueNames

dvla_response_file_matcher = Matcher(
    "dvla_response_file",
    lambda x: "NOTIFY-20180125140000-RSP.TXT" < x <= "NOTIFY-20180125140030-RSP.TXT",
)


@pytest.mark.parametrize(
    "phone_number, sns_callback, sns_callback_args",
    [
        ("+15149301630", sns_success_callback, {}),
        ("+15149301631", sns_success_callback, {}),
        ("+15149301632", sns_failed_callback, {"provider_response": "Phone is currently unreachable/unavailable"}),
        ("+15149301633", sns_failed_callback, {"provider_response": "Phone carrier is currently unreachable/unavailable"}),
    ],
)
@freeze_time("2018-01-25 14:00:30")
def test_make_sns_success_callback(notify_api, mocker, phone_number, sns_callback, sns_callback_args):
    mock_task = mocker.patch("app.celery.research_mode_tasks.process_sns_results")
    some_ref = str(uuid.uuid4())
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    send_sms_response("sns", phone_number, some_ref)

    mock_task.apply_async.assert_called_once_with(ANY, queue=QueueNames.RESEARCH_MODE)
    message_celery = mock_task.apply_async.call_args[0][0][0]
    sns_callback_args.update({"reference": some_ref, "destination": phone_number, "timestamp": timestamp})
    assert message_celery == sns_callback(**sns_callback_args)


@pytest.mark.parametrize(
    "phone_number, pinpoint_callback, pinpoint_callback_args",
    [
        ("+15149301630", pinpoint_delivered_callback, {}),
        ("+15149301631", pinpoint_delivered_callback, {}),
        ("+15149301632", pinpoint_failed_callback, {"provider_response": "Phone is currently unreachable/unavailable"}),
        ("+15149301633", pinpoint_failed_callback, {"provider_response": "Phone carrier is currently unreachable/unavailable"}),
    ],
)
@freeze_time("2018-01-25 14:00:30")
def test_make_pinpoint_success_callback(notify_api, mocker, phone_number, pinpoint_callback, pinpoint_callback_args):
    mock_task = mocker.patch("app.celery.research_mode_tasks.process_pinpoint_results")
    some_ref = str(uuid.uuid4())
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    send_sms_response("pinpoint", phone_number, some_ref)

    mock_task.apply_async.assert_called_once_with(ANY, queue=QueueNames.RESEARCH_MODE)
    message_celery = mock_task.apply_async.call_args[0][0][0]
    pinpoint_callback_args.update({"reference": some_ref, "destination": phone_number, "timestamp": timestamp})
    assert message_celery == pinpoint_callback(**pinpoint_callback_args)


def test_make_ses_callback(notify_api, mocker):
    mock_task = mocker.patch("app.celery.research_mode_tasks.process_ses_results")
    some_ref = str(uuid.uuid4())

    send_email_response(reference=some_ref, to="test@test.com")

    mock_task.apply_async.assert_called_once_with(ANY, queue=QueueNames.RESEARCH_MODE)
    assert mock_task.apply_async.call_args[0][0][0] == ses_notification_callback(some_ref)


@freeze_time("2018-01-25 14:00:30")
def test_create_fake_letter_response_file_uploads_response_file_s3(notify_api, mocker):
    mocker.patch("app.celery.research_mode_tasks.file_exists", return_value=False)
    mock_s3upload = mocker.patch("app.celery.research_mode_tasks.s3upload")

    with requests_mock.Mocker() as request_mock:
        request_mock.post(
            "http://localhost:6011/notifications/letter/dvla",
            content=b"{}",
            status_code=200,
        )

        create_fake_letter_response_file("random-ref")

        mock_s3upload.assert_called_once_with(
            filedata="random-ref|Sent|0|Sorted",
            region=current_app.config["AWS_REGION"],
            bucket_name=current_app.config["DVLA_RESPONSE_BUCKET_NAME"],
            file_location=dvla_response_file_matcher,
        )


@freeze_time("2018-01-25 14:00:30")
def test_create_fake_letter_response_file_calls_dvla_callback_on_development(notify_api, mocker):
    mocker.patch("app.celery.research_mode_tasks.file_exists", return_value=False)
    mocker.patch("app.celery.research_mode_tasks.s3upload")
    mock_task = mocker.patch("app.celery.research_mode_tasks.process_sns_results")

    with set_config_values(notify_api, {"NOTIFY_ENVIRONMENT": "development"}):
        some_ref = str(uuid.uuid4())
        create_fake_letter_response_file(some_ref)

        mock_task.apply_async.assert_called_once_with(ANY, queue=QueueNames.RESEARCH_MODE)
        message = json.loads(mock_task.apply_async.call_args[0][0][0])
        assert message["MessageId"] == some_ref


@freeze_time("2018-01-25 14:00:30")
def test_create_fake_letter_response_file_does_not_call_dvla_callback_on_preview(notify_api, mocker):
    mocker.patch("app.celery.research_mode_tasks.file_exists", return_value=False)
    mocker.patch("app.celery.research_mode_tasks.s3upload")

    with set_config_values(notify_api, {"NOTIFY_ENVIRONMENT": "preview"}):
        with requests_mock.Mocker() as request_mock:
            create_fake_letter_response_file("random-ref")

            assert request_mock.last_request is None


@freeze_time("2018-01-25 14:00:30")
def test_create_fake_letter_response_file_tries_to_create_files_with_other_filenames(notify_api, mocker):
    mock_file_exists = mocker.patch("app.celery.research_mode_tasks.file_exists", side_effect=[True, True, False])
    mock_s3upload = mocker.patch("app.celery.research_mode_tasks.s3upload")

    create_fake_letter_response_file("random-ref")

    assert mock_file_exists.mock_calls == [
        call("test.notify.com-ftp", dvla_response_file_matcher),
        call("test.notify.com-ftp", dvla_response_file_matcher),
        call("test.notify.com-ftp", dvla_response_file_matcher),
    ]
    mock_s3upload.assert_called_once_with(
        filedata=ANY,
        region=ANY,
        bucket_name=ANY,
        file_location=dvla_response_file_matcher,
    )


@freeze_time("2018-01-25 14:00:30")
def test_create_fake_letter_response_file_gives_up_after_thirty_times(notify_api, mocker):
    mock_file_exists = mocker.patch("app.celery.research_mode_tasks.file_exists", return_value=True)
    mock_s3upload = mocker.patch("app.celery.research_mode_tasks.s3upload")

    with pytest.raises(ValueError):
        create_fake_letter_response_file("random-ref")

    assert len(mock_file_exists.mock_calls) == 30
    assert not mock_s3upload.called
