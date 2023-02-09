import base64
import csv
import uuid
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import call

import pytest
from flask import current_app, json
from freezegun import freeze_time
from notifications_python_client.authentication import create_jwt_token

from app import signer
from app.dao.api_key_dao import get_unsigned_secret, save_model_api_key
from app.dao.jobs_dao import dao_get_job_by_id
from app.models import (
    EMAIL_TYPE,
    INTERNATIONAL_SMS_TYPE,
    KEY_TYPE_NORMAL,
    KEY_TYPE_TEAM,
    KEY_TYPE_TEST,
    SCHEDULE_NOTIFICATIONS,
    SMS_TYPE,
    UPLOAD_DOCUMENT,
    ApiKey,
    Notification,
    ScheduledNotification,
)
from app.schema_validation import validate
from app.utils import get_document_url
from app.v2.errors import RateLimitError
from app.v2.notifications.notification_schemas import (
    post_email_response,
    post_sms_response,
)
from tests import create_authorization_header
from tests.app.conftest import (
    create_sample_notification,
    create_sample_template,
    document_download_response,
    random_sized_content,
)
from tests.app.db import (
    create_api_key,
    create_reply_to_email,
    create_service,
    create_service_sms_sender,
    create_service_with_inbound_number,
    create_template,
    create_user,
)
from tests.conftest import set_config, set_config_values


def rows_to_csv(rows):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


class TestSingleEndpointSucceeds:
    @pytest.mark.parametrize("reference", [None, "reference_from_client"])
    def test_post_sms_notification_returns_201(self, notify_api, client, sample_template_with_placeholders, mocker, reference):
        mock_publish = mocker.patch("app.sms_normal_publish.publish")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(sample_template_with_placeholders.id),
            "personalisation": {" Name": "Jo"},
        }
        if reference:
            data.update({"reference": reference})
        auth_header = create_authorization_header(service_id=sample_template_with_placeholders.service_id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_sms_response) == resp_json

        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["phone_number"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]

        assert resp_json["id"] == str(mock_publish_args_unsigned["id"])
        assert resp_json["reference"] == reference
        assert resp_json["content"]["body"] == sample_template_with_placeholders.content.replace("(( Name))", "Jo")
        assert resp_json["content"]["from_number"] == current_app.config["FROM_NUMBER"]
        assert "v2/notifications/{}".format(mock_publish_args_unsigned["id"]) in resp_json["uri"]
        assert resp_json["template"]["id"] == str(sample_template_with_placeholders.id)
        assert resp_json["template"]["version"] == sample_template_with_placeholders.version
        assert (
            "services/{}/templates/{}".format(
                sample_template_with_placeholders.service_id,
                sample_template_with_placeholders.id,
            )
            in resp_json["template"]["uri"]
        )
        assert not resp_json["scheduled_for"]

    def test_post_sms_notification_uses_sms_sender_id_reply_to(
        self, notify_api, client, sample_template_with_placeholders, mocker
    ):
        sms_sender = create_service_sms_sender(service=sample_template_with_placeholders.service, sms_sender="6502532222")
        mock_publish = mocker.patch("app.sms_normal_publish.publish")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(sample_template_with_placeholders.id),
            "personalisation": {" Name": "Jo"},
            "sms_sender_id": str(sms_sender.id),
        }
        auth_header = create_authorization_header(service_id=sample_template_with_placeholders.service_id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_sms_response) == resp_json
        assert resp_json["content"]["from_number"] == "+16502532222"
        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["phone_number"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]

    def test_post_sms_notification_uses_inbound_number_as_sender(self, notify_api, client, notify_db_session, mocker):
        service = create_service_with_inbound_number(inbound_number="1")
        template = create_template(service=service, content="Hello (( Name))\nYour thing is due soon")
        mock_publish = mocker.patch("app.sms_normal_publish.publish")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }
        auth_header = create_authorization_header(service_id=service.id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_sms_response) == resp_json
        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["phone_number"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]
        assert resp_json["content"]["from_number"] == "1"

    def test_post_sms_notification_returns_201_with_sms_sender_id(
        self, notify_api, client, sample_template_with_placeholders, mocker
    ):
        sms_sender = create_service_sms_sender(service=sample_template_with_placeholders.service, sms_sender="123456")
        mock_publish = mocker.patch("app.sms_normal_publish.publish")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(sample_template_with_placeholders.id),
            "personalisation": {" Name": "Jo"},
            "sms_sender_id": str(sms_sender.id),
        }
        auth_header = create_authorization_header(service_id=sample_template_with_placeholders.service_id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_sms_response) == resp_json
        assert resp_json["content"]["from_number"] == sms_sender.sms_sender
        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["phone_number"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]

    def test_post_sms_notification_returns_201_if_allowed_to_send_int_sms(
        self,
        notify_api,
        sample_service,
        sample_template,
        client,
        mocker,
    ):
        mocker.patch("app.sms_normal_publish.publish")

        data = {"phone_number": "+20-12-1234-1234", "template_id": sample_template.id}
        auth_header = create_authorization_header(service_id=sample_service.id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 201
        assert response.headers["Content-type"] == "application/json"

    def test_post_sms_should_publish_supplied_sms_number(self, notify_api, client, sample_template_with_placeholders, mocker):
        mock_publish = mocker.patch("app.sms_normal_publish.publish")

        data = {
            "phone_number": "+16502532222",
            "template_id": str(sample_template_with_placeholders.id),
            "personalisation": {" Name": "Jo"},
        }

        auth_header = create_authorization_header(service_id=sample_template_with_placeholders.service_id)
        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))

        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["phone_number"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]

    @pytest.mark.parametrize("reference", [None, "reference_from_client"])
    def test_post_email_notification_returns_201(notify_api, client, sample_email_template_with_placeholders, mocker, reference):
        mock_publish = mocker.patch("app.email_normal_publish.publish")
        data = {
            "email_address": sample_email_template_with_placeholders.service.users[0].email_address,
            "template_id": sample_email_template_with_placeholders.id,
            "personalisation": {"name": "Bob"},
        }
        if reference:
            data.update({"reference": reference})
        auth_header = create_authorization_header(service_id=sample_email_template_with_placeholders.service_id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_email_response) == resp_json

        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["email_address"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]

        assert resp_json["reference"] == reference
        assert resp_json["content"]["body"] == sample_email_template_with_placeholders.content.replace("((name))", "Bob")
        assert resp_json["content"]["subject"] == sample_email_template_with_placeholders.subject.replace("((name))", "Bob")
        assert resp_json["content"]["from_email"] == "{}@{}".format(
            sample_email_template_with_placeholders.service.email_from,
            current_app.config["NOTIFY_EMAIL_DOMAIN"],
        )
        assert "v2/notifications/{}".format(mock_publish_args_unsigned["id"]) in resp_json["uri"]
        assert resp_json["template"]["id"] == str(sample_email_template_with_placeholders.id)
        assert resp_json["template"]["version"] == sample_email_template_with_placeholders.version
        assert (
            "services/{}/templates/{}".format(
                str(sample_email_template_with_placeholders.service_id),
                str(sample_email_template_with_placeholders.id),
            )
            in resp_json["template"]["uri"]
        )
        assert not resp_json["scheduled_for"]

    def test_post_email_notification_with_valid_reply_to_id_returns_201(self, notify_api, client, sample_email_template, mocker):
        reply_to_email = create_reply_to_email(sample_email_template.service, "test@test.com")
        mock_publish = mocker.patch("app.email_normal_publish.publish")
        data = {
            "email_address": sample_email_template.service.users[0].email_address,
            "template_id": sample_email_template.id,
            "email_reply_to_id": reply_to_email.id,
        }
        auth_header = create_authorization_header(service_id=sample_email_template.service_id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_email_response) == resp_json
        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["email_address"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]


class TestPostNotificationsErrors:
    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "+16502532222"),
            ("email", "email_address", "sample@email.com"),
        ],
    )
    def test_post_notification_returns_400_and_missing_template(
        self, client, sample_service, notification_type, key_send_to, send_to
    ):

        data = {key_send_to: send_to, "template_id": str(uuid.uuid4())}
        auth_header = create_authorization_header(service_id=sample_service.id)

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"

        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["status_code"] == 400
        assert error_json["errors"] == [{"error": "BadRequestError", "message": "Template not found"}]

    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "+16502532222"),
            ("email", "email_address", "sample@email.com"),
            (
                "letter",
                "personalisation",
                {"address_line_1": "The queen", "postcode": "SW1 1AA"},
            ),
        ],
    )
    def test_post_notification_returns_401_and_well_formed_auth_error(
        self, client, sample_template, sample_email_template, notification_type, key_send_to, send_to
    ):
        data = {
            key_send_to: send_to,
            "template_id": str(sample_template.id) if notification_type == "sms" else str(sample_email_template.id),
        }

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json")],
        )

        assert response.status_code == 401
        assert response.headers["Content-type"] == "application/json"
        error_resp = json.loads(response.get_data(as_text=True))
        assert error_resp["status_code"] == 401
        assert error_resp["errors"] == [
            {
                "error": "AuthError",
                "message": "Unauthorized, authentication token must be provided",
            }
        ]

    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "+16502532222"),
            ("email", "email_address", "sample@email.com"),
        ],
    )
    def test_missing_template_id_returns_400(self, client, sample_template, notification_type, key_send_to, send_to):
        data = {key_send_to: send_to}
        auth_header = create_authorization_header(service_id=sample_template.service_id)

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"
        error_resp = json.loads(response.get_data(as_text=True))
        assert error_resp["status_code"] == 400
        assert {
            "error": "ValidationError",
            "message": "template_id is a required property",
        } in error_resp["errors"]

    @pytest.mark.parametrize(
        "notification_type, missing_key",
        [
            ("sms", "phone_number"),
            ("email", "email_address"),
        ],
    )
    def test_missing_recipient_returns_400(self, client, sample_template, sample_email_template, notification_type, missing_key):
        data = {"template_id": str(sample_template.id) if notification_type == "sms" else str(sample_email_template.id)}
        auth_header = create_authorization_header(service_id=sample_template.service_id)

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"
        error_resp = json.loads(response.get_data(as_text=True))
        assert error_resp["status_code"] == 400
        assert {
            "error": "ValidationError",
            "message": f"{missing_key} is a required property",
        } in error_resp["errors"]

    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "+16502532222"),
            ("email", "email_address", "sample@email.com"),
        ],
    )
    def test_extra_field_returns_400(
        self, client, sample_template, sample_email_template, notification_type, key_send_to, send_to
    ):
        data = {
            key_send_to: send_to,
            "template_id": str(sample_template.id) if notification_type == "sms" else str(sample_email_template.id),
            "test_field": "not wanted",
        }
        auth_header = create_authorization_header(service_id=sample_template.service_id)

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"
        error_resp = json.loads(response.get_data(as_text=True))
        assert error_resp["status_code"] == 400
        assert {
            "error": "ValidationError",
            "message": "Additional properties are not allowed (test_field was unexpected)",
        } in error_resp["errors"]

    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "6502532222"),
            ("email", "email_address", "sample@email.com"),
        ],
    )
    def test_returns_a_429_limit_exceeded_if_rate_limit_exceeded(
        self, notify_api, client, sample_service, mocker, notification_type, key_send_to, send_to
    ):
        sample = create_template(service=sample_service, template_type=notification_type)
        save_mock = mocker.patch("app.v2.notifications.post_notifications.db_save_and_send_notification")
        mocker.patch(
            "app.v2.notifications.post_notifications.check_rate_limiting",
            side_effect=RateLimitError("LIMIT", "INTERVAL", "TYPE"),
        )

        data = {key_send_to: send_to, "template_id": str(sample.id)}

        auth_header = create_authorization_header(service_id=sample.service_id)

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        error = json.loads(response.data)["errors"][0]["error"]
        message = json.loads(response.data)["errors"][0]["message"]
        status_code = json.loads(response.data)["status_code"]
        assert response.status_code == 429
        assert error == "RateLimitError"
        assert message == "Exceeded rate limit for key type TYPE of LIMIT requests per INTERVAL seconds"
        assert status_code == 429

        assert not save_mock.called

    def test_post_sms_notification_returns_400_if_not_allowed_to_send_int_sms(
        self,
        client,
        notify_db_session,
    ):
        service = create_service(service_permissions=[SMS_TYPE])
        template = create_template(service=service)

        data = {"phone_number": "+20-12-1234-1234", "template_id": template.id}
        auth_header = create_authorization_header(service_id=service.id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"

        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["status_code"] == 400
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "Cannot send to international mobile numbers",
            }
        ]

    def test_post_sms_notification_with_archived_reply_to_id_returns_400(self, client, sample_template):
        archived_sender = create_service_sms_sender(sample_template.service, "12345", is_default=False, archived=True)
        data = {
            "phone_number": "+16502532222",
            "template_id": sample_template.id,
            "sms_sender_id": archived_sender.id,
        }
        auth_header = create_authorization_header(service_id=sample_template.service_id)
        response = client.post(
            path="v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert (
            "sms_sender_id {} does not exist in database for service id {}".format(archived_sender.id, sample_template.service_id)
            in resp_json["errors"][0]["message"]
        )
        assert "BadRequestError" in resp_json["errors"][0]["error"]

    @pytest.mark.parametrize(
        "recipient,label,permission_type, notification_type,expected_error",
        [
            ("6502532222", "phone_number", "email", "sms", "text messages"),
            ("someone@test.com", "email_address", "sms", "email", "emails"),
        ],
    )
    def test_post_sms_notification_returns_400_if_not_allowed_to_send_notification(
        self,
        notify_db_session,
        client,
        recipient,
        label,
        permission_type,
        notification_type,
        expected_error,
    ):
        service = create_service(service_permissions=[permission_type])
        sample_template_without_permission = create_template(service=service, template_type=notification_type)
        data = {label: recipient, "template_id": sample_template_without_permission.id}
        auth_header = create_authorization_header(service_id=sample_template_without_permission.service.id)

        response = client.post(
            path="/v2/notifications/{}".format(sample_template_without_permission.template_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"

        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["status_code"] == 400
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "Service is not allowed to send {}".format(expected_error),
            }
        ]

    @pytest.mark.parametrize("restricted", [True, False])
    def test_post_sms_notification_returns_400_if_number_not_safelisted(self, notify_db_session, client, restricted):
        service = create_service(restricted=restricted, service_permissions=[SMS_TYPE, INTERNATIONAL_SMS_TYPE])
        template = create_template(service=service)
        create_api_key(service=service, key_type="team")

        data = {
            "phone_number": "+16132532235",
            "template_id": template.id,
        }
        auth_header = create_authorization_header(service_id=service.id, key_type="team")

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["status_code"] == 400
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": f"Can’t send to this recipient using a team-only API key (service {service.id}) "
                f'- see {get_document_url("en", "keys.html#team-and-safelist")}',
            }
        ]

    def test_post_notification_raises_bad_request_if_not_valid_notification_type(self, client, sample_service):
        auth_header = create_authorization_header(service_id=sample_service.id)
        response = client.post(
            "/v2/notifications/foo",
            data="{}",
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 404
        error_json = json.loads(response.get_data(as_text=True))
        assert "The requested URL was not found on the server." in error_json["message"]

    @pytest.mark.parametrize("notification_type", ["sms", "email"])
    def test_post_notification_with_wrong_type_of_sender(
        self, client, sample_template, sample_email_template, notification_type, fake_uuid
    ):
        if notification_type == EMAIL_TYPE:
            template = sample_email_template
            form_label = "sms_sender_id"
            data = {
                "email_address": "test@test.com",
                "template_id": str(sample_email_template.id),
                form_label: fake_uuid,
            }
        elif notification_type == SMS_TYPE:
            template = sample_template
            form_label = "email_reply_to_id"
            data = {
                "phone_number": "+16502532222",
                "template_id": str(template.id),
                form_label: fake_uuid,
            }
        auth_header = create_authorization_header(service_id=template.service_id)

        response = client.post(
            path="/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert "Additional properties are not allowed ({} was unexpected)".format(form_label) in resp_json["errors"][0]["message"]
        assert "ValidationError" in resp_json["errors"][0]["error"]

    def test_post_email_notification_with_invalid_reply_to_id_returns_400(self, client, sample_email_template, mocker, fake_uuid):
        mocker.patch("app.email_normal_publish.publish")
        data = {
            "email_address": sample_email_template.service.users[0].email_address,
            "template_id": sample_email_template.id,
            "email_reply_to_id": fake_uuid,
        }
        auth_header = create_authorization_header(service_id=sample_email_template.service_id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert (
            "email_reply_to_id {} does not exist in database for service id {}".format(
                fake_uuid, sample_email_template.service_id
            )
            in resp_json["errors"][0]["message"]
        )
        assert "BadRequestError" in resp_json["errors"][0]["error"]

    def test_post_email_notification_with_archived_reply_to_id_returns_400(self, client, sample_email_template, mocker):
        archived_reply_to = create_reply_to_email(
            sample_email_template.service,
            "reply_to@test.com",
            is_default=False,
            archived=True,
        )
        mocker.patch("app.email_normal_publish.publish")
        data = {
            "email_address": "test@test.com",
            "template_id": sample_email_template.id,
            "email_reply_to_id": archived_reply_to.id,
        }
        auth_header = create_authorization_header(service_id=sample_email_template.service_id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert (
            "email_reply_to_id {} does not exist in database for service id {}".format(
                archived_reply_to.id, sample_email_template.service_id
            )
            in resp_json["errors"][0]["message"]
        )
        assert "BadRequestError" in resp_json["errors"][0]["error"]

    @pytest.mark.parametrize(
        "personalisation_size, expected_success",
        [
            (1024 * 50 + 100, False),
            (1024 * 50 - 100, True),
        ],
    )
    def test_post_email_notification_with_personalisation_too_large(
        self, notify_api, client, sample_email_template_with_placeholders, mocker, personalisation_size, expected_success
    ):
        mocked = mocker.patch("app.email_normal_publish.publish")

        data = {
            "email_address": sample_email_template_with_placeholders.service.users[0].email_address,
            "template_id": sample_email_template_with_placeholders.id,
            "personalisation": {"name": random_sized_content(size=personalisation_size)},
            "reference": "reference_from_client",
        }

        auth_header = create_authorization_header(service_id=sample_email_template_with_placeholders.service_id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        if expected_success:
            assert mocked.called
            assert response.status_code == 201
        else:
            resp_json = json.loads(response.get_data(as_text=True))
            assert not mocked.called
            assert response.status_code == 400
            assert "ValidationError" in resp_json["errors"][0]["error"]
            assert (
                f"Personalisation variables size of {personalisation_size} bytes is greater than allowed limit of 51200 bytes"
                in resp_json["errors"][0]["message"]
            )

    def test_post_notification_returns_400_when_get_json_throws_exception(self, client, sample_email_template):
        auth_header = create_authorization_header(service_id=sample_email_template.service_id)
        response = client.post(
            path="v2/notifications/email",
            data="[",
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 400

    def test_too_long_sms_returns_400(self, client, notify_db, notify_db_session):
        service = create_service(sms_daily_limit=10, message_limit=100)
        auth_header = create_authorization_header(service_id=service.id)

        max_size_template_content = (
            612 - len(service.name) - 2
        )  # 612 is the max size of an sms, minus the service name that we append, minus 2 for the space and the colon which we append (i.e. "service name: ")
        # create a template with content that is too long
        template = create_sample_template(
            notify_db, notify_db_session, service=service, template_type="sms", content="a" * (max_size_template_content + 1)
        )

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps({"phone_number": "+16502532222", "template_id": template.id}),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"
        error_resp = json.loads(response.get_data(as_text=True))
        assert error_resp["status_code"] == 400
        assert "has a character count greater than" in str(response.data)


@pytest.mark.parametrize(
    "recipient, notification_type",
    [
        ("simulate-delivered@notification.canada.ca", EMAIL_TYPE),
        ("simulate-delivered-2@notification.canada.ca", EMAIL_TYPE),
        ("simulate-delivered-3@notification.canada.ca", EMAIL_TYPE),
        ("6132532222", "sms"),
        ("6132532223", "sms"),
        ("6132532224", "sms"),
    ],
)
def test_should_not_persist_or_send_notification_if_simulated_recipient(
    client, recipient, notification_type, sample_email_template, sample_template, mocker
):
    mock_publish = mocker.patch("app.{}_normal_publish.publish".format(notification_type))

    if notification_type == "sms":
        data = {"phone_number": recipient, "template_id": str(sample_template.id)}
    else:
        data = {"email_address": recipient, "template_id": str(sample_email_template.id)}

    auth_header = create_authorization_header(service_id=sample_email_template.service_id)

    response = client.post(
        path="/v2/notifications/{}".format(notification_type),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )

    assert response.status_code == 201
    assert json.loads(response.get_data(as_text=True))["id"]
    mock_publish.assert_not_called()
    assert Notification.query.count() == 0


@pytest.mark.parametrize(
    "notification_type, key_send_to, send_to",
    [
        ("sms", "phone_number", "6502532222"),
        ("email", "email_address", "sample@email.com"),
    ],
)
@pytest.mark.parametrize("process_type", ["priority", "bulk"])
def test_send_notification_uses_appropriate_queue_according_to_template_process_type(
    notify_api,
    client,
    sample_service,
    mocker,
    notification_type,
    key_send_to,
    send_to,
    process_type,
):
    mock_publish = mocker.patch("app.{}_{}_publish.publish".format(notification_type, process_type))

    sample = create_template(
        service=sample_service,
        template_type=notification_type,
        process_type=process_type,
    )
    data = {key_send_to: send_to, "template_id": str(sample.id)}

    auth_header = create_authorization_header(service_id=sample.service_id)

    response = client.post(
        path="/v2/notifications/{}".format(notification_type),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )

    assert response.status_code == 201
    mock_publish_args = mock_publish.call_args.args[0]
    mock_publish_args_unsigned = signer.verify(mock_publish_args)
    assert mock_publish_args_unsigned["to"] == data[key_send_to]


class TestRestrictedServices:
    @pytest.mark.parametrize(
        "notification_type,to_key,to,response_code",
        [
            ("sms", "phone_number", "+16132532235", 201),
            ("email", "email_address", "test@example.com", 201),
            ("sms", "phone_number", "+16132532230", 400),
            ("email", "email_address", "bad@example.com", 400),
        ],
    )
    def test_team_keys_only_send_to_team_members(
        self, notify_db_session, client, mocker, notify_api, notification_type, to_key, to, response_code
    ):
        service = create_service(restricted=True, service_permissions=[EMAIL_TYPE, SMS_TYPE, INTERNATIONAL_SMS_TYPE])
        user = create_user(mobile_number="+16132532235", email="test@example.com")
        service.users = [user]
        template = create_template(service=service, template_type=notification_type)
        create_api_key(service=service, key_type="team")
        redis_publish = mocker.patch(f"app.{notification_type}_normal_publish.publish")
        data = {
            to_key: to,
            "template_id": template.id,
        }
        auth_header = create_authorization_header(service_id=service.id, key_type="team")

        response = client.post(
            path=f"/v2/notifications/{notification_type}",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == response_code
        assert json.loads(response.get_data(as_text=True))
        if response_code == 201:
            assert redis_publish.called
        else:
            assert redis_publish.called is False

    @pytest.mark.parametrize(
        "notification_type,to_key,to_a, to_b,response_code",
        [
            ("email", "email_address", "foo@example.com", "bar@example.com", 201),
            ("sms", "phone_number", "+16132532231", "+16132532232", 201),
            ("email", "email_address", "foo@example.com", "error@example.com", 400),
            ("sms", "phone_number", "+16132532231", "+16132532233", 400),
        ],
    )
    def test_team_keys_only_send_to_team_members_bulk_endpoint(
        self, notify_db_session, client, mocker, notification_type, to_key, to_a, to_b, response_code
    ):
        service = create_service(
            restricted=True,
            service_permissions=[EMAIL_TYPE, SMS_TYPE],
        )
        user_1 = create_user(mobile_number="+16132532231", email="foo@example.com")
        user_2 = create_user(mobile_number="+16132532232", email="bar@example.com")
        service.users = [user_1, user_2]
        template = create_template(service=service, template_type=notification_type)
        create_api_key(service=service, key_type="team")
        job_id = str(uuid.uuid4())
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=job_id)

        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([[to_key], [to_a], [to_b]]),
        }
        auth_header = create_authorization_header(service_id=service.id, key_type="team")
        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == response_code
        assert json.loads(response.get_data(as_text=True))


class TestSchedulingSends:
    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "6502532222"),
            ("email", "email_address", "sample@email.com"),
        ],
    )
    @freeze_time("2017-05-14 14:00:00")
    def test_post_notification_with_scheduled_for(self, client, notify_db_session, notification_type, key_send_to, send_to):
        service = create_service(
            service_name=str(uuid.uuid4()),
            service_permissions=[EMAIL_TYPE, SMS_TYPE, SCHEDULE_NOTIFICATIONS],
        )
        template = create_template(service=service, template_type=notification_type)
        data = {
            key_send_to: send_to,
            "template_id": str(template.id) if notification_type == EMAIL_TYPE else str(template.id),
            "scheduled_for": "2017-05-14 14:15",
        }
        auth_header = create_authorization_header(service_id=service.id)

        response = client.post(
            "/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        scheduled_notification = ScheduledNotification.query.filter_by(notification_id=resp_json["id"]).all()
        assert len(scheduled_notification) == 1
        assert resp_json["id"] == str(scheduled_notification[0].notification_id)
        assert resp_json["scheduled_for"] == "2017-05-14 14:15"

    @pytest.mark.parametrize(
        "notification_type, key_send_to, send_to",
        [
            ("sms", "phone_number", "6502532222"),
            ("email", "email_address", "sample@email.com"),
        ],
    )
    @freeze_time("2017-05-14 14:00:00")
    def test_post_notification_raises_bad_request_if_service_not_invited_to_schedule(
        self,
        client,
        sample_template,
        sample_email_template,
        notification_type,
        key_send_to,
        send_to,
    ):
        data = {
            key_send_to: send_to,
            "template_id": str(sample_email_template.id) if notification_type == EMAIL_TYPE else str(sample_template.id),
            "scheduled_for": "2017-05-14 14:15",
        }
        auth_header = create_authorization_header(service_id=sample_template.service_id)

        response = client.post(
            "/v2/notifications/{}".format(notification_type),
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "Cannot schedule notifications (this feature is invite-only)",
            }
        ]


class TestSendingDocuments:
    @pytest.mark.parametrize(
        "filename, file_data, sending_method",
        [
            ("good name.txt", "VGV4dCBjb250ZW50IGhlcmU=", "attach"),
            ("good name.txt", "VGV4dCBjb250ZW50IGhlcmU=", "link"),
        ],
    )
    def test_post_notification_with_document_upload(
        self, notify_api, client, notify_db_session, mocker, filename, file_data, sending_method
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        if sending_method == "link":
            content = "Document: ((document))"
        template = create_template(service=service, template_type="email", content=content)

        statsd_mock = mocker.patch("app.v2.notifications.post_notifications.statsd_client")
        mock_publish = mocker.patch("app.email_normal_publish.publish")
        document_download_mock = mocker.patch("app.v2.notifications.post_notifications.document_download_client.upload_document")
        document_response = document_download_response({"sending_method": sending_method, "mime_type": "text/plain"})
        document_download_mock.return_value = document_response
        decoded_file = base64.b64decode(file_data)

        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {
                "document": {
                    "file": file_data,
                    "filename": filename,
                    "sending_method": sending_method,
                }
            },
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_email_response) == resp_json
        document_download_mock.assert_called_once_with(
            service.id,
            {"file": decoded_file, "filename": filename, "sending_method": sending_method},
        )

        mock_publish_args = mock_publish.call_args.args[0]
        mock_publish_args_unsigned = signer.verify(mock_publish_args)
        assert mock_publish_args_unsigned["to"] == data["email_address"]
        assert mock_publish_args_unsigned["id"] == resp_json["id"]

        if sending_method == "link":
            assert resp_json["content"]["body"] == f"Document: {document_response}"
        else:
            assert resp_json["content"]["body"] == "See attached file."

        assert statsd_mock.incr.call_args_list == [
            call("attachments.nb-attachments.count-1"),
            call("attachments.nb-attachments", count=1),
            call(f"attachments.services.{service.id}", count=1),
            call(f"attachments.templates.{template.id}", count=1),
            call(f"attachments.sending-method.{sending_method}"),
            call("attachments.file-type.text/plain"),
            call("attachments.file-size.0-1mb"),
        ]

    @pytest.mark.parametrize(
        "filename, sending_method, attachment_size, expected_success",
        [
            ("attached_file.txt", "attach", 1024 * 1024 * 10 + 100, False),
            ("linked_file.txt", "link", 1024 * 1024 * 10 + 100, False),
            ("attached_file.txt", "attach", 1024 * 1024 * 10 - 100, True),
            ("linked_file.txt", "link", 1024 * 1024 * 10 - 100, True),
        ],
    )
    def test_post_notification_with_document_too_large(
        self, notify_api, client, notify_db_session, mocker, filename, sending_method, attachment_size, expected_success
    ):
        mocked = mocker.patch("app.email_normal_publish.publish")
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        if sending_method == "link":
            content = "Document: ((document))"
        template = create_template(service=service, template_type="email", content=content)

        mocker.patch("app.v2.notifications.post_notifications.statsd_client")
        document_download_mock = mocker.patch("app.v2.notifications.post_notifications.document_download_client.upload_document")
        document_response = document_download_response({"sending_method": sending_method, "mime_type": "text/plain"})
        document_download_mock.return_value = document_response

        file_data = random_sized_content(size=attachment_size)
        encoded_file = base64.b64encode(file_data.encode()).decode()

        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {
                "document": {
                    "file": encoded_file,
                    "filename": filename,
                    "sending_method": sending_method,
                }
            },
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        if expected_success:
            assert mocked.called
            assert response.status_code == 201
        else:
            resp_json = json.loads(response.get_data(as_text=True))
            assert not mocked.called
            assert response.status_code == 400
            assert "ValidationError" in resp_json["errors"][0]["error"]
            assert filename in resp_json["errors"][0]["message"]
            assert "and greater than allowed limit of" in resp_json["errors"][0]["message"]

    @pytest.mark.parametrize(
        "sending_method, attachment_number, expected_success",
        [
            ("attach", 9, True),
            ("link", 9, True),
            ("attach", 10, True),
            ("link", 10, True),
            ("attach", 11, False),
            ("link", 11, False),
        ],
    )
    def test_post_notification_with_too_many_documents(
        self, notify_api, client, notify_db_session, mocker, sending_method, attachment_number, expected_success
    ):
        mocked = mocker.patch("app.email_normal_publish.publish")
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        template_content = "See attached file.\n"
        if sending_method == "link":
            for i in range(0, attachment_number):
                template_content = template_content + f"Document: ((doc-{i}))\n"
        template = create_template(service=service, template_type="email", content=template_content)

        mocker.patch("app.v2.notifications.post_notifications.statsd_client")
        document_download_mock = mocker.patch("app.v2.notifications.post_notifications.document_download_client.upload_document")
        document_response = document_download_response({"sending_method": sending_method, "mime_type": "text/plain"})
        document_download_mock.return_value = document_response

        documents = {}
        for i in range(0, attachment_number):
            file_data = random_sized_content()
            encoded_file = base64.b64encode(file_data.encode()).decode()
            documents[f"doc-{i}"] = {
                "file": encoded_file,
                "filename": f"doc-{i}",
                "sending_method": sending_method,
            }

        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": documents,
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        if expected_success:
            assert mocked.called
            assert response.status_code == 201
        else:
            resp_json = json.loads(response.get_data(as_text=True))
            assert not mocked.called
            assert response.status_code == 400
            assert "ValidationError" in resp_json["errors"][0]["error"]
            assert (
                f"File number exceed allowed limits of 10 with number of {attachment_number}."
                in resp_json["errors"][0]["message"]
            )

    @pytest.mark.parametrize(
        "filename, file_data, sending_method",
        [
            ("", "VGV4dCBjb250ZW50IGhlcmU=", "attach"),
            ("1", "VGV4dCBjb250ZW50IGhlcmU=", "attach"),
        ],
    )
    def test_post_notification_with_document_upload_bad_filename(
        self, client, notify_db_session, filename, file_data, sending_method
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        template = create_template(service=service, template_type="email", content=content)
        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {
                "document": {
                    "file": file_data,
                    "filename": filename,
                    "sending_method": sending_method,
                }
            },
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert "ValidationError" in resp_json["errors"][0]["error"]
        assert filename in resp_json["errors"][0]["message"]
        assert "too short" in resp_json["errors"][0]["message"]

    def test_post_notification_with_document_upload_long_filename(
        self,
        client,
        notify_db_session,
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        template = create_template(service=service, template_type="email", content=content)
        file_data = "VGV4dCBjb250ZW50IGhlcmU="
        filename = "a" * 256
        sending_method = "attach"

        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {
                "document": {
                    "file": file_data,
                    "filename": filename,
                    "sending_method": sending_method,
                }
            },
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert "ValidationError" in resp_json["errors"][0]["error"]
        assert filename in resp_json["errors"][0]["message"]
        assert "too long" in resp_json["errors"][0]["message"]

    @pytest.mark.parametrize(
        "file_data, sending_method",
        [
            ("VGV4dCBjb250ZW50IGhlcmU=", "attach"),
        ],
    )
    def test_post_notification_with_document_upload_filename_required_check(
        self, client, notify_db_session, file_data, sending_method
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        template = create_template(service=service, template_type="email", content=content)
        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {"document": {"file": file_data, "sending_method": sending_method}},
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert "ValidationError" in resp_json["errors"][0]["error"]
        assert "filename is a required property" in resp_json["errors"][0]["message"]

    @pytest.mark.parametrize(
        "file_data",
        [
            ("VGV4dCBjb250ZW50IGhlcmU="),
        ],
    )
    def test_post_notification_with_document_upload_missing_sending_method(
        self,
        client,
        notify_db_session,
        file_data,
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        template = create_template(service=service, template_type="email", content=content)
        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {"document": {"file": file_data}},
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert "ValidationError" in resp_json["errors"][0]["error"]
        assert "sending_method is a required property" in resp_json["errors"][0]["message"]

    @pytest.mark.parametrize(
        "file_data, sending_method, filename",
        [
            ("VGV4dCBjb250ZW50IGhlcmU=", "attch", "1.txt"),
        ],
    )
    def test_post_notification_with_document_upload_bad_sending_method(
        self, client, notify_db_session, file_data, sending_method, filename
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        template = create_template(service=service, template_type="email", content=content)
        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {
                "document": {
                    "file": file_data,
                    "filename": filename,
                    "sending_method": sending_method,
                }
            },
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert f"personalisation {sending_method} is not one of [attach, link]" in resp_json["errors"][0]["message"]

    @pytest.mark.parametrize(
        "file_data, message",
        [
            ("abc", "Incorrect padding"),
            ("🤡", "string argument should contain only ASCII characters"),
        ],
    )
    def test_post_notification_with_document_upload_not_base64_file(
        self,
        client,
        notify_db_session,
        file_data,
        message,
    ):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        content = "See attached file."
        template = create_template(service=service, template_type="email", content=content)
        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {
                "document": {
                    "file": file_data,
                    "sending_method": "attach",
                    "filename": "1.txt",
                }
            },
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        resp_json = json.loads(response.get_data(as_text=True))
        assert f"{message} : Error decoding base64 field" in resp_json["errors"][0]["message"]

    def test_post_notification_with_document_upload_simulated(self, client, notify_db_session, mocker):
        service = create_service(service_permissions=[EMAIL_TYPE, UPLOAD_DOCUMENT])
        template = create_template(service=service, template_type="email", content="Document: ((document))")

        mocker.patch("app.email_normal_publish.publish")
        document_download_mock = mocker.patch("app.v2.notifications.post_notifications.document_download_client")
        document_download_mock.get_upload_url.return_value = "https://document-url"

        data = {
            "email_address": "simulate-delivered@notification.canada.ca",
            "template_id": template.id,
            "personalisation": {"document": {"file": "abababab", "sending_method": "link"}},
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 201
        resp_json = json.loads(response.get_data(as_text=True))
        assert validate(resp_json, post_email_response) == resp_json

        assert resp_json["content"]["body"] == "Document: https://document-url/test-document"

    def test_post_notification_without_document_upload_permission(self, client, notify_db_session, mocker):
        service = create_service(service_permissions=[EMAIL_TYPE])
        template = create_template(service=service, template_type="email", content="Document: ((document))")

        mocker.patch("app.email_normal_publish.publish")
        document_download_mock = mocker.patch("app.v2.notifications.post_notifications.document_download_client")
        document_download_mock.upload_document.return_value = document_download_response()

        data = {
            "email_address": service.users[0].email_address,
            "template_id": template.id,
            "personalisation": {"document": {"file": "abababab"}},
        }

        auth_header = create_authorization_header(service_id=service.id)
        response = client.post(
            path="v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400


class TestSMSSendFragments:
    def test_post_sms_enough_fragments_left(self, notify_api, client, notify_db, notify_db_session, mocker):
        mocker.patch("app.sms_normal_publish.publish")
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content=500 * "a", service=service, template_type="sms")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }
        for x in range(2):
            create_sample_notification(notify_db, notify_db_session, service=service)
        auth_header = create_authorization_header(service_id=template.service_id)

        with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
            response = client.post(
                path="/v2/notifications/sms",
                data=json.dumps(data),
                headers=[("Content-Type", "application/json"), auth_header],
            )
        assert response.status_code == 201

    def test_post_sms_not_enough_fragments_left(self, notify_api, client, notify_db, notify_db_session, mocker):
        mocker.patch("app.sms_normal_publish.publish")
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content=500 * "a", service=service, template_type="sms")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)
        auth_header = create_authorization_header(service_id=template.service_id)

        with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
            response = client.post(
                path="/v2/notifications/sms",
                data=json.dumps(data),
                headers=[("Content-Type", "application/json"), auth_header],
            )
        assert response.status_code == 429

    def test_post_sms_not_enough_fragments_left_FF_SPIKE_SMS_DAILY_LIMIT_false(
        self, notify_api, client, notify_db, notify_db_session, mocker
    ):
        mocker.patch("app.sms_normal_publish.publish")
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content=500 * "a", service=service, template_type="sms")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)
        auth_header = create_authorization_header(service_id=template.service_id)

        with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": False, "REDIS_ENABLED": True}):
            response = client.post(
                path="/v2/notifications/sms",
                data=json.dumps(data),
                headers=[("Content-Type", "application/json"), auth_header],
            )
        assert response.status_code == 201


class TestSMSFragmentCounter:
    # Testing API one-off:
    #   - Sending using TEST, NORMAL, and TEAM API keys with a simulated phone number should not count towards limits
    # TODO: update these params when we fix https://github.com/cds-snc/notification-planning/issues/855 and remove the xfao;
    @pytest.mark.parametrize(
        "key_type", [KEY_TYPE_TEST, KEY_TYPE_NORMAL, pytest.param(KEY_TYPE_TEAM, marks=pytest.mark.xfail(raises=AssertionError))]
    )
    def test_API_ONEOFF_post_sms_with_test_key_does_not_count_towards_limits(
        self, notify_api, client, notify_db, notify_db_session, mocker, key_type
    ):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        increment_todays_requested_sms_count = mocker.patch("app.notifications.validators.increment_todays_requested_sms_count")

        def __send_sms():
            api_key = ApiKey(
                service=service,
                name="test_key",
                created_by=template.created_by,
                key_type=key_type,
            )
            save_model_api_key(api_key)

            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                response = client.post(
                    path="/v2/notifications/sms",
                    data=json.dumps(data),
                    headers=[
                        ("Content-Type", "application/json"),
                        ("Authorization", f"ApiKey-v1 {get_unsigned_secret(api_key.id)}"),
                    ],
                )
                return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        data = {
            "phone_number": "+16132532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }

        response = __send_sms()

        assert response.status_code == 201
        assert not increment_todays_requested_sms_count.called

    # Testing API BULK:
    #   - Sending using TEST API key with ALL simulated phone numbers should not count towards limits
    # TODO: update these params when we fix https://github.com/cds-snc/notification-planning/issues/855 and remove the xfao;
    @pytest.mark.parametrize(
        "key_type", [KEY_TYPE_TEST, KEY_TYPE_NORMAL, pytest.param(KEY_TYPE_TEAM, marks=pytest.mark.xfail(raises=AssertionError))]
    )
    def test_API_BULK_post_sms_with_test_key_does_not_count_towards_limits(
        self, notify_api, client, notify_db, notify_db_session, mocker, key_type
    ):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))
        increment_todays_requested_sms_count = mocker.patch("app.notifications.validators.increment_todays_requested_sms_count")

        def __send_sms():
            api_key = ApiKey(
                service=service,
                name="test_key",
                created_by=template.created_by,
                key_type=key_type,
            )
            save_model_api_key(api_key)

            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                response = client.post(
                    path="/v2/notifications/bulk",
                    data=json.dumps(data),
                    headers=[
                        ("Content-Type", "application/json"),
                        ("Authorization", f"ApiKey-v1 {get_unsigned_secret(api_key.id)}"),
                    ],
                )
                return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        data = {
            "name": "Bulk send name",
            "template_id": str(template.id),
            "rows": [["phone number"], ["+16132532222"], ["+16132532223"], ["+16132532224"]],
        }

        response = __send_sms()

        assert response.status_code == 201
        assert not increment_todays_requested_sms_count.called

    # Testing API BULK:
    #   - Throw an error if a user mixes testing and non-testing numbers with a LIVE or TEAM key
    #   - Allow mixing if its a TEST key
    @pytest.mark.parametrize("key_type", [KEY_TYPE_TEST, KEY_TYPE_NORMAL, KEY_TYPE_TEAM])
    def test_API_BULK_post_sms_with_mixed_numbers(self, notify_api, client, notify_db, notify_db_session, mocker, key_type):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))
        increment_todays_requested_sms_count = mocker.patch("app.notifications.validators.increment_todays_requested_sms_count")

        def __send_sms():
            api_key = ApiKey(
                service=service,
                name="test_key",
                created_by=template.created_by,
                key_type=key_type,
            )
            save_model_api_key(api_key)

            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):

                response = client.post(
                    path="/v2/notifications/bulk",
                    data=json.dumps(data),
                    headers=[
                        ("Content-Type", "application/json"),
                        ("Authorization", f"ApiKey-v1 {get_unsigned_secret(api_key.id)}"),
                    ],
                )
                return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        data = {
            "name": "Bulk send name",
            "template_id": str(template.id),
            "rows": [["phone number"], ["+16132532222"], ["+16132532223"], ["+16135555555"]],
        }

        response = __send_sms()
        resp_json = json.loads(response.get_data(as_text=True))

        # If the key is a test key, then the request should succeed
        if key_type == KEY_TYPE_TEST:
            assert response.status_code == 201
            assert not increment_todays_requested_sms_count.called
        else:
            assert resp_json["errors"][0]["error"] == "BadRequestError"

    # Testing ADMIN one-off:
    #   - Sending using TEST phone numbers (i.e. +16132532222)  should not count towards limits
    def test_ADMIN_ONEOFF_post_sms_with_test_phone_number_does_not_count_towards_limits(
        self, notify_api, client, notify_db, notify_db_session, mocker
    ):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.service.send_notification.send_notification_to_queue")
        increment_todays_requested_sms_count = mocker.patch("app.notifications.validators.increment_todays_requested_sms_count")

        def __send_sms():
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/send-notification",
                    json={
                        "to": "+16132532222",
                        "template_id": str(template.id),
                        "created_by": service.users[0].id,
                        "personalisation": {"var": "var"},
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, tempalte
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content="a" * 400, service=service, template_type="sms")

        response = __send_sms()

        assert response.status_code == 201
        assert not increment_todays_requested_sms_count.called

    # Testing ADMIN CSV:
    #   - Sending using ALL TEST phone numbers (i.e. +16132532222) should succeed and not increment their daily usage
    #   - Sending using test+non-test phone numbers should fail
    @pytest.mark.parametrize(
        "expected_status_code, phone_numbers",
        [
            (201, "\r\n+16132532222\r\n+16132532222"),
            (400, "\r\n+16132532222\r\n+15555555555"),
        ],
    )
    def test_ADMIN_CSV_post_sms_with_test_phone_number_does_not_count_towards_limits(
        self, notify_api, client, notify_db, notify_db_session, mocker, expected_status_code, phone_numbers
    ):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.service.send_notification.send_notification_to_queue")
        mocker.patch("app.celery.tasks.process_job.apply_async")
        mocker.patch(
            "app.job.rest.get_job_from_s3",
            return_value=f"phone number{phone_numbers}",
        )
        increment_todays_requested_sms_count = mocker.patch("app.notifications.validators.increment_todays_requested_sms_count")

        def __send_sms():
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                mocker.patch(
                    "app.job.rest.get_job_metadata_from_s3",
                    return_value={
                        "template_id": str(template.id),
                        "original_file_name": "thisisatest.csv",
                        "notification_count": "1",
                        "valid": "True",
                    },
                )

                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/job",
                    json={
                        "id": str(uuid.uuid4()),
                        "created_by": service.users[0].id,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, template
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")

        response = __send_sms()  # 8/10 fragments
        assert response.status_code == expected_status_code
        assert not increment_todays_requested_sms_count.called


class TestEmailsAndLimitsForSMSFragments:
    # API
    def test_API_ONEOFF_sends_warning_emails_and_blocks_sends(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms():
            auth_header = create_authorization_header(service_id=template.service_id)
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                response = client.post(
                    path="/v2/notifications/sms",
                    data=json.dumps(data),
                    headers=[("Content-Type", "application/json"), auth_header],
                )
                return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 7 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms()  # send 8th fragment
        assert send_warning_email.called

        __send_sms()  # Send 9th fragment
        __send_sms()  # Send 10th fragment
        assert send_limit_reached_email.called

        response = __send_sms()  # send the 11th fragment
        assert response.status_code == 429  # Ensure send is blocked

    def test_API_ONEOFF_cant_hop_over_limit_using_3_fragment_sms(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")

        def __send_sms():
            auth_header = create_authorization_header(service_id=template.service_id)
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                response = client.post(
                    path="/v2/notifications/sms",
                    data=json.dumps(data),
                    headers=[("Content-Type", "application/json"), auth_header],
                )
                return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 5 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="a" * 400, service=service, template_type="sms")
        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }
        for x in range(5):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms()  # send 1 sms (3 fragments) should be at 80%
        assert send_warning_email.called

        response = __send_sms()  # send one more, puts us at 11/10 fragments
        assert response.status_code == 429  # Ensure send is blocked

    def test_API_BULK_sends_warning_emails_and_blocks_sends(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms():
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                data = {
                    "name": "job_name",
                    "template_id": str(template.id),
                    "rows": [["phone number"], ["9025551234"]],
                }

                response = client.post(
                    "/v2/notifications/bulk",
                    data=json.dumps(data),
                    headers=[
                        ("Content-Type", "application/json"),
                        create_authorization_header(service_id=template.service_id),
                    ],
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 7 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms()  # send 8th fragment
        assert send_warning_email.called

        __send_sms()  # Send 9th fragment
        __send_sms()  # Send 10th fragment
        assert send_limit_reached_email.called

        response = __send_sms()  # send the 11th fragment
        assert response.status_code == 400  # Ensure send is blocked - not sure why we send a 400 here and a 429 everywhere else

    def test_API_BULK_cant_hop_over_limit_1_fragment(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms(number_to_send=1):
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                numbers = [["9025551234"]] * number_to_send
                data = {
                    "name": "job_name",
                    "template_id": str(template.id),
                    "rows": [["phone number"], *numbers],
                }

                response = client.post(
                    "/v2/notifications/bulk",
                    data=json.dumps(data),
                    headers=[
                        ("Content-Type", "application/json"),
                        create_authorization_header(service_id=template.service_id),
                    ],
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 7 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms(1)  # send 1 sms (1 fragment) should be at 80%
        assert send_warning_email.called

        response = __send_sms(3)  # attempt to send over limit (11 with max 10)
        assert response.status_code == 400

        __send_sms(2)  # attempt to send at limit (10 with max 10)
        assert send_limit_reached_email.called

        response = __send_sms(1)  # attempt to send over limit (11 with max 10)1
        assert response.status_code == 400  # Ensure send is blocked - not sure why we send a 400 here and a 429 everywhere else

    def test_API_BULK_cant_hop_over_limit_2_fragment(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")

        def __send_sms(number_to_send=1):
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                numbers = [["9025551234"]] * number_to_send
                data = {
                    "name": "job_name",
                    "template_id": str(template.id),
                    "rows": [["phone number"], *numbers],
                }

                response = client.post(
                    "/v2/notifications/bulk",
                    data=json.dumps(data),
                    headers=[
                        ("Content-Type", "application/json"),
                        create_authorization_header(service_id=template.service_id),
                    ],
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="A" * 400, service=service, template_type="sms")
        for x in range(5):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms(1)  # 8/10 fragments used
        assert send_warning_email.called

        response = __send_sms(3)  # attempt to send over limit
        assert response.status_code == 400

        response = __send_sms(2)  # attempt to send over limit
        assert response.status_code == 400

    # ADMIN
    def test_ADMIN_ONEOFF_sends_warning_emails_and_blocks_sends(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")

        mocker.patch("app.service.send_notification.send_notification_to_queue")
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms():
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/send-notification",
                    json={
                        "to": "9025551234",
                        "template_id": str(template.id),
                        "created_by": service.users[0].id,
                        "personalisation": {"var": "var"},
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 7 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms()  # 8/10 fragments used
        assert send_warning_email.called

        __send_sms()  # 9/10 fragments used
        __send_sms()  # 10/10 fragments used
        assert send_limit_reached_email.called

        response = __send_sms()  # 11/10 fragments
        assert response.status_code == 429  # Ensure send is blocked

    def test_ADMIN_ONEOFF_cant_hop_over_limit_using_3_fragment_sms(
        self, notify_api, client, notify_db, notify_db_session, mocker
    ):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")

        mocker.patch("app.service.send_notification.send_notification_to_queue")
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")

        def __send_sms():
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/send-notification",
                    json={
                        "to": "9025551234",
                        "template_id": str(template.id),
                        "created_by": service.users[0].id,
                        "personalisation": {"var": "var"},
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 5 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="a" * 400, service=service, template_type="sms")
        for x in range(5):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms()  # 8/10 fragments
        assert send_warning_email.called

        response = __send_sms()  # 11/10 fragments
        assert response.status_code == 429  # Ensure send is blocked

    def test_ADMIN_CSV_sends_warning_emails_and_blocks_sends(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.service.send_notification.send_notification_to_queue")
        mocker.patch("app.celery.tasks.process_job.apply_async")
        mocker.patch(
            "app.job.rest.get_job_from_s3",
            return_value="phone number\r\n6502532222",
        )
        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms():
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                mocker.patch(
                    "app.job.rest.get_job_metadata_from_s3",
                    return_value={
                        "template_id": str(template.id),
                        "original_file_name": "thisisatest.csv",
                        "notification_count": "1",
                        "valid": "True",
                    },
                )

                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/job",
                    json={
                        "id": str(uuid.uuid4()),
                        "created_by": service.users[0].id,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 7 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms()  # 8/10 fragments
        assert send_warning_email.called

        __send_sms()  # 9/10 fragments
        __send_sms()  # 10/10 fragments
        assert send_limit_reached_email.called

        response = __send_sms()  # 11/10 fragments
        assert response.status_code == 429  # Ensure send is blocked

    def test_ADMIN_CSV_cant_hop_over_limit_using_1_fragment_sms(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.service.send_notification.send_notification_to_queue")
        mocker.patch("app.celery.tasks.process_job.apply_async")

        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms(number_to_send=1):
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                phone_numbers = "\r\n6502532222" * number_to_send
                mocker.patch("app.job.rest.get_job_from_s3", return_value=f"phone number{phone_numbers}")
                mocker.patch(
                    "app.job.rest.get_job_metadata_from_s3",
                    return_value={
                        "template_id": str(template.id),
                        "original_file_name": "thisisatest.csv",
                        "notification_count": f"{number_to_send}",
                        "valid": "True",
                    },
                )

                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/job",
                    json={
                        "id": str(uuid.uuid4()),
                        "created_by": service.users[0].id,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 7 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="Hello", service=service, template_type="sms")
        for x in range(7):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms(1)  # 8/10 fragments
        assert send_warning_email.called

        response = __send_sms(3)  # 11/10 fragments
        assert response.status_code == 429

        __send_sms(2)  # 10/10 fragments
        assert send_limit_reached_email.called

        response = __send_sms(1)  # 11/10 fragments
        assert response.status_code == 429  # Ensure send is blocked - not sure why we send a 400 here and a 429 everywhere else

    def test_ADMIN_CSV_cant_hop_over_limit_using_2_fragment_sms(self, notify_api, client, notify_db, notify_db_session, mocker):
        # test setup
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.service.send_notification.send_notification_to_queue")
        mocker.patch("app.celery.tasks.process_job.apply_async")

        send_warning_email = mocker.patch("app.notifications.validators.send_near_sms_limit_email")
        send_limit_reached_email = mocker.patch("app.notifications.validators.send_sms_limit_reached_email")

        def __send_sms(number_to_send=1):
            with set_config_values(notify_api, {"FF_SPIKE_SMS_DAILY_LIMIT": True, "REDIS_ENABLED": True}):
                phone_numbers = "\r\n6502532222" * number_to_send
                mocker.patch("app.job.rest.get_job_from_s3", return_value=f"phone number{phone_numbers}")
                mocker.patch(
                    "app.job.rest.get_job_metadata_from_s3",
                    return_value={
                        "template_id": str(template.id),
                        "original_file_name": "thisisatest.csv",
                        "notification_count": f"{number_to_send}",
                        "valid": "True",
                    },
                )

                token = create_jwt_token(
                    current_app.config["ADMIN_CLIENT_SECRET"], client_id=current_app.config["ADMIN_CLIENT_USER_NAME"]
                )
                response = client.post(
                    f"/service/{template.service_id}/job",
                    json={
                        "id": str(uuid.uuid4()),
                        "created_by": service.users[0].id,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
            return response

        # Create a service, Set limit to 10 fragments
        service = create_service(sms_daily_limit=10, message_limit=100)

        # Create 6 notifications in the db
        template = create_sample_template(notify_db, notify_db_session, content="A" * 200, service=service, template_type="sms")
        for x in range(6):
            create_sample_notification(notify_db, notify_db_session, service=service)

        __send_sms(1)  # 8/10 fragments
        assert send_warning_email.called

        response = __send_sms(2)  # 12/10 fragments
        assert response.status_code == 429

        __send_sms(1)  # 10/10 fragments
        assert send_limit_reached_email.called

        response = __send_sms(1)  # 11/10 fragments
        assert response.status_code == 429  # Ensure send is blocked - not sure why we send a 400 here and a 429 everywhere else


class TestBulkSend:
    @pytest.mark.parametrize("args", [{}, {"rows": [1, 2], "csv": "foo"}], ids=["no args", "both args"])
    def test_post_bulk_with_invalid_data_arguments(
        self,
        client,
        sample_email_template,
        args,
    ):
        data = {"name": "job_name", "template_id": str(sample_email_template.id)} | args

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "You should specify either rows or csv",
            }
        ]

    def test_post_bulk_with_invalid_reply_to_id(self, client, sample_email_template):
        data = {
            "name": "job_name",
            "template_id": str(sample_email_template.id),
            "rows": [["email address"], ["bob@example.com"]],
            "reply_to_id": "foo",
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "ValidationError",
                "message": "reply_to_id is not a valid UUID",
            }
        ]

    def test_post_bulk_with_non_existing_reply_to_id_for_email(self, client, sample_email_template, fake_uuid):
        data = {
            "name": "job_name",
            "template_id": str(sample_email_template.id),
            "rows": [["email address"], ["bob@example.com"]],
            "reply_to_id": fake_uuid,
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": f"email_reply_to_id {fake_uuid} does not exist in database for service id {sample_email_template.service_id}",
            }
        ]

    def test_post_bulk_with_non_existing_reply_to_id_for_sms(self, client, sms_code_template, fake_uuid):
        data = {
            "name": "job_name",
            "template_id": str(sms_code_template.id),
            "rows": [["phone number", "verify_code"], ["bob@example.com", "123"]],
            "reply_to_id": fake_uuid,
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), create_authorization_header(service_id=sms_code_template.service_id)],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": f"sms_sender_id {fake_uuid} does not exist in database for service id {sms_code_template.service_id}",
            }
        ]

    def test_post_bulk_flags_if_name_is_missing(self, client, sample_email_template):
        data = {"template_id": str(sample_email_template.id), "csv": "foo"}

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [{"error": "ValidationError", "message": "name is a required property"}]

    @pytest.mark.parametrize(
        "scheduled_for, expected_message",
        [
            (42, "scheduled_for 42 is not of type string, null"),
            (
                "foo",
                "scheduled_for datetime format is invalid. It must be a valid "
                "ISO8601 date time format, "
                "https://en.wikipedia.org/wiki/ISO_8601",
            ),
            ("2016-01-01T10:04:00", "scheduled_for datetime cannot be in the past"),
            ("2016-01-05T10:06:00", "scheduled_for datetime can only be up to 96 hours in the future"),
        ],
    )
    @freeze_time("2016-01-01 10:05:00")
    def test_post_bulk_with_invalid_scheduled_for(self, client, sample_email_template, scheduled_for, expected_message):
        data = {"name": "job_name", "template_id": str(sample_email_template.id), "scheduled_for": scheduled_for, "rows": [1, 2]}

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [{"error": "ValidationError", "message": expected_message}]

    def test_post_bulk_with_non_existing_template(self, client, fake_uuid, sample_email_template):
        data = {"name": "job_name", "template_id": fake_uuid, "rows": [1, 2]}

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [{"error": "BadRequestError", "message": "Template not found"}]

    def test_post_bulk_with_archived_template(self, client, fake_uuid, notify_db, notify_db_session):
        template = create_sample_template(notify_db, notify_db_session, archived=True)
        data = {"name": "job_name", "template_id": template.id, "rows": [1, 2]}

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [{"error": "BadRequestError", "message": f"Template {template.id} has been deleted"}]

    @pytest.mark.parametrize(
        "permission_type, notification_type, expected_error",
        [
            ("email", "sms", "text messages"),
            ("sms", "email", "emails"),
        ],
    )
    def test_post_bulk_returns_400_if_not_allowed_to_send_notification_type(
        self,
        notify_db_session,
        client,
        permission_type,
        notification_type,
        expected_error,
    ):
        service = create_service(service_permissions=[permission_type])
        sample_template_without_permission = create_template(service=service, template_type=notification_type)
        data = {"name": "job_name", "template_id": sample_template_without_permission.id, "rows": [1, 2]}
        auth_header = create_authorization_header(service_id=sample_template_without_permission.service.id)

        response = client.post(
            path="/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )

        assert response.status_code == 400
        assert response.headers["Content-type"] == "application/json"

        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["status_code"] == 400
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": f"Service is not allowed to send {expected_error}",
            }
        ]

    @pytest.mark.parametrize("data_type", ["rows", "csv"])
    @pytest.mark.parametrize(
        "template_type, content, row_header, expected_error",
        [
            ("email", "Hello!", ["foo"], "email address"),
            ("email", "Hello ((name))!", ["foo"], "email address, name"),
            ("sms", "Hello ((name))!", ["foo"], "name, phone number"),
            ("sms", "Hello ((name))!", ["foo"], "name, phone number"),
            ("sms", "Hello ((name))!", ["name"], "phone number"),
            ("sms", "Hello ((name))!", ["NAME"], "phone number"),
        ],
    )
    def test_post_bulk_flags_missing_column_headers(
        self, client, notify_db, notify_db_session, data_type, template_type, content, row_header, expected_error
    ):
        template = create_sample_template(notify_db, notify_db_session, content=content, template_type=template_type)
        data = {"name": "job_name", "template_id": template.id}
        rows = [row_header, ["bar"]]
        if data_type == "csv":
            data["csv"] = rows_to_csv(rows)
        else:
            data["rows"] = rows

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [{"error": "BadRequestError", "message": f"Missing column headers: {expected_error}"}]

    @pytest.mark.parametrize(
        "template_type, content, row_header, expected_error",
        [
            (
                "email",
                "Hello!",
                ["email address", "email address"],
                "email address",
            ),
            (
                "email",
                "Hello ((name))!",
                ["email address", "email_address", "name"],
                "email address, email_address",
            ),
            ("sms", "Hello!", ["phone number", "phone number"], "phone number"),
            (
                "sms",
                "Hello!",
                ["phone number", "phone_number"],
                "phone number, phone_number",
            ),
            (
                "sms",
                "Hello ((name))!",
                ["phone number", "phone_number", "name"],
                "phone number, phone_number",
            ),
        ],
    )
    def test_post_bulk_flags_duplicate_recipient_column_headers(
        self,
        client,
        notify_db,
        notify_db_session,
        template_type,
        content,
        row_header,
        expected_error,
    ):
        template = create_sample_template(notify_db, notify_db_session, content=content, template_type=template_type)
        data = {"name": "job_name", "template_id": template.id, "rows": [row_header, ["bar"]]}

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [{"error": "BadRequestError", "message": f"Duplicate column headers: {expected_error}"}]

    def test_post_bulk_flags_too_many_rows(self, client, sample_email_template, notify_api):
        data = {
            "name": "job_name",
            "template_id": sample_email_template.id,
            "csv": rows_to_csv([["email address"], ["foo@example.com"], ["bar@example.com"]]),
        }

        with set_config(notify_api, "CSV_MAX_ROWS", 1):
            response = client.post(
                "/v2/notifications/bulk",
                data=json.dumps(data),
                headers=[
                    ("Content-Type", "application/json"),
                    create_authorization_header(service_id=sample_email_template.service_id),
                ],
            )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "Too many rows. Maximum number of rows allowed is 1",
            }
        ]

    def test_post_bulk_flags_recipient_not_in_safelist_with_team_api_key(self, client, sample_email_template):
        data = {
            "name": "job_name",
            "template_id": sample_email_template.id,
            "csv": rows_to_csv([["email address"], ["foo@example.com"], ["bar@example.com"]]),
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id, key_type="team"),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "You cannot send to these recipients because you used a team and safelist API key.",
            }
        ]

    def test_post_bulk_flags_recipient_not_in_safelist_with_restricted_service(self, client, notify_db, notify_db_session):
        service = create_service(restricted=True)
        template = create_sample_template(notify_db, notify_db_session, service=service, template_type="email")
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([["email address"], ["foo@example.com"], ["bar@example.com"]]),
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=template.service_id, key_type="team"),
            ],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "You cannot send to these recipients because your service is in trial mode. You can only send to members of your team and your safelist.",
            }
        ]

    def test_post_bulk_flags_not_enough_remaining_messages(self, client, notify_db, notify_db_session, mocker):
        service = create_service(message_limit=10)
        template = create_sample_template(notify_db, notify_db_session, service=service, template_type="email")
        messages_count_mock = mocker.patch(
            "app.v2.notifications.post_notifications.fetch_todays_total_message_count", return_value=9
        )
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([["email address"], ["foo@example.com"], ["bar@example.com"]]),
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "You only have 1 remaining messages before you reach your daily limit. You've tried to send 2 messages.",
            }
        ]
        messages_count_mock.assert_called_once()

    def test_post_bulk_flags_not_enough_remaining_sms_messages(self, notify_api, client, notify_db, notify_db_session, mocker):
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, service=service, template_type="sms")
        mocker.patch("app.v2.notifications.post_notifications.fetch_todays_total_message_count", return_value=9)
        messages_count_mock = mocker.patch(
            "app.v2.notifications.post_notifications.fetch_todays_requested_sms_count", return_value=9
        )
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([["phone number"], ["6135551234"], ["6135551234"]]),
        }

        with set_config(notify_api, "FF_SPIKE_SMS_DAILY_LIMIT", True):
            response = client.post(
                "/v2/notifications/bulk",
                data=json.dumps(data),
                headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
            )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "You only have 1 remaining sms message parts before you reach your daily limit. You've tried to send 2 message parts.",
            }
        ]
        messages_count_mock.assert_called_once()

    def test_post_bulk_flags_not_enough_remaining_sms_message_parts(
        self, notify_api, client, notify_db, notify_db_session, mocker
    ):
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content=500 * "a", service=service, template_type="sms")
        mocker.patch("app.v2.notifications.post_notifications.fetch_todays_total_message_count", return_value=9)
        mocker.patch("app.v2.notifications.post_notifications.fetch_todays_requested_sms_count", return_value=9)
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([["phone number"], ["6135551234"]]),
        }

        with set_config(notify_api, "FF_SPIKE_SMS_DAILY_LIMIT", True):
            response = client.post(
                "/v2/notifications/bulk",
                data=json.dumps(data),
                headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
            )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "You only have 1 remaining sms message parts before you reach your daily limit. You've tried to send 4 message parts.",
            }
        ]

    def test_post_bulk_does_not_flag_not_enough_remaining_sms_message_parts_with_FF_SPIKE_SMS_DAILY_LIMIT_false(
        self, notify_api, client, notify_db, notify_db_session, notify_user, mocker
    ):
        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, content=500 * "a", service=service, template_type="sms")
        mocker.patch("app.v2.notifications.post_notifications.fetch_todays_requested_sms_count", return_value=9)
        job_id = str(uuid.uuid4())
        mocker.patch("app.v2.notifications.post_notifications.upload_job_to_s3", return_value=job_id)
        mocker.patch("app.v2.notifications.post_notifications.process_job.apply_async")
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([["phone number"], ["6135551234"], ["6135551234"], ["6135551234"], ["6135551234"]]),
        }

        with set_config(notify_api, "FF_SPIKE_SMS_DAILY_LIMIT", False):
            response = client.post(
                "/v2/notifications/bulk",
                data=json.dumps(data),
                headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
            )

        assert response.status_code == 201

    @pytest.mark.parametrize("data_type", ["rows", "csv"])
    def test_post_bulk_flags_rows_with_errors(self, client, notify_db, notify_db_session, data_type):
        template = create_sample_template(notify_db, notify_db_session, template_type="email", content="Hello ((name))")
        data = {"name": "job_name", "template_id": template.id}
        rows = [
            ["email address", "name"],
            ["foo@example.com", "Foo"],
            ["bar@example.com"],
            ["nope", "nope"],
            ["baz@example.com", ""],
            ["baz@example.com", " "],
        ]
        if data_type == "csv":
            data["csv"] = rows_to_csv(rows)
        else:
            data["rows"] = rows

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), create_authorization_header(service_id=template.service_id)],
        )

        assert response.status_code == 400
        error_json = json.loads(response.get_data(as_text=True))
        assert error_json["errors"] == [
            {
                "error": "BadRequestError",
                "message": "Some rows have errors. Row 1 - `name`: Missing. Row 2 - `email address`: invalid recipient. Row 3 - `name`: Missing. Row 4 - `name`: Missing.",
            }
        ]

    @pytest.mark.parametrize("data_type", ["rows", "csv"])
    @pytest.mark.parametrize("is_scheduled", [True, False])
    @pytest.mark.parametrize("use_sender_id", [True, False])
    @pytest.mark.parametrize("has_default_reply_to", [True, False])
    def test_post_bulk_creates_job_and_dispatches_celery_task(
        self,
        client,
        sample_email_template,
        mocker,
        notify_user,
        notify_api,
        data_type,
        is_scheduled,
        use_sender_id,
        has_default_reply_to,
    ):
        data = {"name": "job_name", "template_id": sample_email_template.id}
        rows = [["email address"], ["foo@example.com"]]
        if data_type == "csv":
            data["csv"] = rows_to_csv(rows)
        else:
            data["rows"] = rows

        if is_scheduled:
            scheduled_for = datetime.utcnow() + timedelta(days=1)
            data["scheduled_for"] = scheduled_for.isoformat()
        if has_default_reply_to:
            create_reply_to_email(sample_email_template.service, "test@test.com")
        if use_sender_id:
            reply_to_email = create_reply_to_email(sample_email_template.service, "custom@test.com", is_default=False)
            data["reply_to_id"] = reply_to_email.id

        api_key = create_api_key(service=sample_email_template.service)
        job_id = str(uuid.uuid4())
        upload_to_s3 = mocker.patch("app.v2.notifications.post_notifications.upload_job_to_s3", return_value=job_id)
        process_job = mocker.patch("app.v2.notifications.post_notifications.process_job.apply_async")

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=sample_email_template.service_id),
            ],
        )

        upload_to_s3.assert_called_once_with(sample_email_template.service_id, "email address\r\nfoo@example.com")
        if not is_scheduled:
            process_job.assert_called_once_with([str(job_id)], queue="job-tasks")
        else:
            process_job.assert_not_called()

        job = dao_get_job_by_id(job_id)
        assert str(job.id) == job_id
        assert job.service_id == sample_email_template.service_id
        assert job.template_id == sample_email_template.id
        assert job.notification_count == 1
        assert job.template_version == sample_email_template.version
        assert job.job_status == "scheduled" if is_scheduled else "pending"
        assert job.original_file_name == "job_name"
        if is_scheduled:
            assert job.scheduled_for == scheduled_for
        else:
            assert job.scheduled_for is None
        assert job.api_key_id == api_key.id
        if use_sender_id:
            assert job.sender_id == reply_to_email.id
        else:
            assert job.sender_id is None

        assert response.status_code == 201

        assert json.loads(response.get_data(as_text=True)) == {
            "data": {
                "api_key": {
                    "id": str(api_key.id),
                    "key_type": "normal",
                    "name": api_key.name,
                },
                "archived": False,
                "created_at": f"{job.created_at.isoformat()}+00:00",
                "created_by": {"id": str(notify_user.id), "name": notify_user.name},
                "id": job_id,
                "job_status": "scheduled" if is_scheduled else "pending",
                "notification_count": 1,
                "original_file_name": "job_name",
                "processing_finished": None,
                "processing_started": None,
                "scheduled_for": f"{scheduled_for.isoformat()}+00:00" if is_scheduled else None,
                "service": str(sample_email_template.service_id),
                "service_name": {"name": sample_email_template.service.name},
                "template": str(sample_email_template.id),
                "template_version": sample_email_template.version,
                "updated_at": None,
                "sender_id": str(reply_to_email.id) if use_sender_id else None,
            }
        }

    def test_post_bulk_with_too_large_sms_fails(self, client, notify_db, notify_db_session, mocker):
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))

        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(notify_db, notify_db_session, service=service, template_type="sms", content="a" * 612)
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv([["phone number"], ["+16502532222"]]),
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=service.id),
            ],
        )
        assert response.status_code == 400
        assert "has a character count greater than" in str(response.data)

    @pytest.mark.parametrize(
        "row_data, failure_row",
        [
            ([["phone number", "Name"], ["+16502532222", "a" * 612]], 1),
            ([["phone number", "Name"], ["+16502532222", "a"], ["+16502532222", "a" * 612]], 2),
        ],
    )
    def test_post_bulk_with_too_large_sms_fail_and_shows_correct_row(
        self, client, notify_db, notify_db_session, mocker, row_data, failure_row
    ):
        mocker.patch("app.sms_normal_publish.publish")
        mocker.patch("app.v2.notifications.post_notifications.create_bulk_job", return_value=str(uuid.uuid4()))

        service = create_service(sms_daily_limit=10, message_limit=100)
        template = create_sample_template(
            notify_db, notify_db_session, service=service, template_type="sms", content="Hello (( Name))\nYour thing is due soon"
        )
        data = {
            "name": "job_name",
            "template_id": template.id,
            "csv": rows_to_csv(row_data),
        }

        response = client.post(
            "/v2/notifications/bulk",
            data=json.dumps(data),
            headers=[
                ("Content-Type", "application/json"),
                create_authorization_header(service_id=service.id),
            ],
        )
        assert response.status_code == 400
        assert "has a character count greater than" in str(response.data)
        assert "row #{}".format(failure_row) in str(response.data)


class TestBatchPriorityLanes:
    @pytest.mark.parametrize("process_type", ["bulk", "normal", "priority"])
    def test_sms_each_queue_is_used(self, notify_api, client, service_factory, mocker, process_type):
        mock_redisQueue_SMS_BULK = mocker.patch("app.sms_bulk_publish.publish")
        mock_redisQueue_SMS_NORMAL = mocker.patch("app.sms_normal_publish.publish")
        mock_redisQueue_SMS_PRIORITY = mocker.patch("app.sms_priority_publish.publish")

        service = service_factory.get("one")
        template = create_template(service=service, content="Hello (( Name))\nYour thing is due soon", process_type=process_type)

        data = {
            "phone_number": "+16502532222",
            "template_id": str(template.id),
            "personalisation": {" Name": "Jo"},
        }

        auth_header = create_authorization_header(service_id=template.service_id)

        response = client.post(
            path="/v2/notifications/sms",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201

        if process_type == "bulk":
            assert mock_redisQueue_SMS_BULK.called
        elif process_type == "normal":
            assert mock_redisQueue_SMS_NORMAL.called
        elif process_type == "priority":
            assert mock_redisQueue_SMS_PRIORITY.called

    @pytest.mark.parametrize("process_type", ["bulk", "normal", "priority"])
    def test_email_each_queue_is_used(self, notify_api, client, mocker, service_factory, process_type):
        mock_redisQueue_EMAIL_BULK = mocker.patch("app.email_bulk_publish.publish")
        mock_redisQueue_EMAIL_NORMAL = mocker.patch("app.email_normal_publish.publish")
        mock_redisQueue_EMAIL_PRIORITY = mocker.patch("app.email_priority_publish.publish")

        service = service_factory.get("one")
        template = create_template(
            service=service, template_type="email", content="Hello (( Name))\nYour thing is due soon", process_type=process_type
        )

        data = {
            "email_address": template.service.users[0].email_address,
            "template_id": str(template.id),
            "personalisation": {"name": "Jo"},
        }

        auth_header = create_authorization_header(service_id=template.service_id)

        response = client.post(
            path="/v2/notifications/email",
            data=json.dumps(data),
            headers=[("Content-Type", "application/json"), auth_header],
        )
        assert response.status_code == 201
        # pytest.set_trace()
        if process_type == "bulk":
            assert mock_redisQueue_EMAIL_BULK.called
        elif process_type == "normal":
            assert mock_redisQueue_EMAIL_NORMAL.called
        elif process_type == "priority":
            assert mock_redisQueue_EMAIL_PRIORITY.called
