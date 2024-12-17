import base64
import json
import uuid
from datetime import datetime, timedelta

import botocore
import pytest
import requests_mock
from freezegun import freeze_time
from notifications_utils import (
    EMAIL_CHAR_COUNT_LIMIT,
    SMS_CHAR_COUNT_LIMIT,
    TEMPLATE_NAME_CHAR_COUNT_LIMIT,
)
from PyPDF2.utils import PdfReadError

from app.dao.organisation_dao import dao_update_organisation
from app.dao.service_permissions_dao import dao_add_service_permission
from app.dao.templates_dao import dao_get_template_by_id, dao_redact_template
from app.models import EMAIL_TYPE, LETTER_TYPE, SMS_TYPE, Template, TemplateHistory
from app.template.rest import should_template_be_redacted
from tests import create_authorization_header
from tests.app.conftest import (
    create_sample_template,
    create_sample_template_without_email_permission,
    create_sample_template_without_letter_permission,
    create_sample_template_without_sms_permission,
)
from tests.app.db import (
    create_letter_contact,
    create_notification,
    create_organisation,
    create_service,
    create_template,
    create_template_folder,
    save_notification,
)
from tests.conftest import set_config_values


@pytest.mark.parametrize(
    "template_type, subject",
    [
        (SMS_TYPE, None),
        (EMAIL_TYPE, "subject"),
    ],
)
def test_should_create_a_new_template_for_a_service(client, sample_user, template_type, subject, sample_template_category):
    service = create_service(service_permissions=[template_type])
    data = {
        "name": "my template",
        "template_type": template_type,
        "content": "template <b>content</b>",
        "service": str(service.id),
        "created_by": str(sample_user.id),
        "template_category_id": str(sample_template_category.id),
    }
    if subject:
        data.update({"subject": subject})
    if template_type == LETTER_TYPE:
        data.update({"postage": "first"})
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    assert response.status_code == 201
    json_resp = json.loads(response.get_data(as_text=True))
    assert json_resp["data"]["name"] == "my template"
    assert json_resp["data"]["template_type"] == template_type
    assert json_resp["data"]["content"] == "template <b>content</b>"
    assert json_resp["data"]["service"] == str(service.id)
    assert json_resp["data"]["id"]
    assert json_resp["data"]["version"] == 1
    assert json_resp["data"]["process_type"] == "normal"
    assert json_resp["data"]["created_by"] == str(sample_user.id)
    assert json_resp["data"]["template_category_id"] == str(sample_template_category.id)
    if subject:
        assert json_resp["data"]["subject"] == "subject"
    else:
        assert not json_resp["data"]["subject"]

    if template_type == LETTER_TYPE:
        assert json_resp["data"]["postage"] == "first"
    else:
        assert not json_resp["data"]["postage"]

    template = Template.query.get(json_resp["data"]["id"])
    from app.schemas import template_schema

    assert sorted(json_resp["data"]) == sorted(template_schema.dump(template))


def test_create_a_new_template_for_a_service_adds_folder_relationship(client, sample_service):
    parent_folder = create_template_folder(service=sample_service, name="parent folder")

    data = {
        "name": "my template",
        "template_type": "sms",
        "content": "template <b>content</b>",
        "service": str(sample_service.id),
        "created_by": str(sample_service.users[0].id),
        "parent_folder_id": str(parent_folder.id),
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    assert response.status_code == 201
    template = Template.query.filter(Template.name == "my template").first()
    assert template.folder == parent_folder


@pytest.mark.parametrize(
    "template_type, expected_postage",
    [(SMS_TYPE, None), (EMAIL_TYPE, None), (LETTER_TYPE, "second")],
)
def test_create_a_new_template_for_a_service_adds_postage_for_letters_only(
    client, sample_service, template_type, expected_postage
):
    dao_add_service_permission(service_id=sample_service.id, permission=LETTER_TYPE)
    data = {
        "name": "my template",
        "template_type": template_type,
        "content": "template <b>content</b>",
        "service": str(sample_service.id),
        "created_by": str(sample_service.users[0].id),
    }
    if template_type in [EMAIL_TYPE, LETTER_TYPE]:
        data["subject"] = "Hi, I have good news"

    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    assert response.status_code == 201
    template = Template.query.filter(Template.name == "my template").first()
    assert template.postage == expected_postage


def test_create_template_should_return_400_if_folder_is_for_a_different_service(client, sample_service):
    service2 = create_service(service_name="second service")
    parent_folder = create_template_folder(service=service2)

    data = {
        "name": "my template",
        "template_type": "sms",
        "content": "template <b>content</b>",
        "service": str(sample_service.id),
        "created_by": str(sample_service.users[0].id),
        "parent_folder_id": str(parent_folder.id),
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    assert response.status_code == 400
    assert json.loads(response.get_data(as_text=True))["message"] == "parent_folder_id not found"


def test_create_template_should_return_400_if_folder_does_not_exist(client, sample_service):
    data = {
        "name": "my template",
        "template_type": "sms",
        "content": "template <b>content</b>",
        "service": str(sample_service.id),
        "created_by": str(sample_service.users[0].id),
        "parent_folder_id": str(uuid.uuid4()),
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    assert response.status_code == 400
    assert json.loads(response.get_data(as_text=True))["message"] == "parent_folder_id not found"


def test_should_raise_error_if_service_does_not_exist_on_create(client, sample_user, fake_uuid):
    data = {
        "name": "my template",
        "template_type": SMS_TYPE,
        "content": "template content",
        "service": fake_uuid,
        "created_by": str(sample_user.id),
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(fake_uuid),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    json_resp = json.loads(response.get_data(as_text=True))
    assert response.status_code == 404
    assert json_resp["result"] == "error"
    assert json_resp["message"] == "No result found"


@pytest.mark.parametrize(
    "permissions, template_type, subject, expected_error",
    [
        (
            [EMAIL_TYPE],
            SMS_TYPE,
            None,
            {"template_type": ["Creating text message templates is not allowed"]},
        ),
        (
            [SMS_TYPE],
            EMAIL_TYPE,
            "subject",
            {"template_type": ["Creating email templates is not allowed"]},
        ),
        (
            [SMS_TYPE],
            LETTER_TYPE,
            "subject",
            {"template_type": ["Creating letter templates is not allowed"]},
        ),
    ],
)
def test_should_raise_error_on_create_if_no_permission(client, sample_user, permissions, template_type, subject, expected_error):
    service = create_service(service_permissions=permissions)
    data = {
        "name": "my template",
        "template_type": template_type,
        "content": "template content",
        "service": str(service.id),
        "created_by": str(sample_user.id),
    }
    if subject:
        data.update({"subject": subject})

    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    json_resp = json.loads(response.get_data(as_text=True))
    assert response.status_code == 403
    assert json_resp["result"] == "error"
    assert json_resp["message"] == expected_error


@pytest.mark.parametrize(
    "template_factory, expected_error",
    [
        (
            create_sample_template_without_sms_permission,
            {"template_type": ["Updating text message templates is not allowed"]},
        ),
        (
            create_sample_template_without_email_permission,
            {"template_type": ["Updating email templates is not allowed"]},
        ),
        (
            create_sample_template_without_letter_permission,
            {"template_type": ["Updating letter templates is not allowed"]},
        ),
    ],
)
def test_should_be_error_on_update_if_no_permission(
    client, sample_user, template_factory, expected_error, notify_db, notify_db_session
):
    template_without_permission = template_factory(notify_db, notify_db_session)
    data = {"content": "new template content", "created_by": str(sample_user.id)}

    data = json.dumps(data)
    auth_header = create_authorization_header()

    update_response = client.post(
        "/service/{}/template/{}".format(template_without_permission.service_id, template_without_permission.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )

    json_resp = json.loads(update_response.get_data(as_text=True))
    assert update_response.status_code == 403
    assert json_resp["result"] == "error"
    assert json_resp["message"] == expected_error


def test_should_error_if_created_by_missing(client, sample_user, sample_service):
    service_id = str(sample_service.id)
    data = {
        "name": "my template",
        "template_type": SMS_TYPE,
        "content": "template content",
        "service": service_id,
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(service_id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    json_resp = json.loads(response.get_data(as_text=True))
    assert response.status_code == 400
    assert json_resp["errors"][0]["error"] == "ValidationError"
    assert json_resp["errors"][0]["message"] == "created_by is a required property"


def test_should_be_error_if_service_does_not_exist_on_update(client, fake_uuid):
    data = {"name": "my template"}
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template/{}".format(fake_uuid, fake_uuid),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    json_resp = json.loads(response.get_data(as_text=True))
    assert response.status_code == 404
    assert json_resp["result"] == "error"
    assert json_resp["message"] == "No result found"


@pytest.mark.parametrize("template_type", [EMAIL_TYPE, LETTER_TYPE])
def test_must_have_a_subject_on_an_email_or_letter_template(client, sample_user, sample_service, template_type):
    data = {
        "name": "my template",
        "template_type": template_type,
        "content": "template content",
        "service": str(sample_service.id),
        "created_by": str(sample_user.id),
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    json_resp = json.loads(response.get_data(as_text=True))
    assert json_resp["errors"][0]["error"] == "ValidationError"
    assert json_resp["errors"][0]["message"] == "subject is a required property"


def test_update_should_update_a_template(client, sample_user):
    service = create_service(service_permissions=[LETTER_TYPE])
    template = create_template(service, template_type="letter", postage="second")
    data = {
        "content": "my template has new content, swell!",
        "created_by": str(sample_user.id),
        "postage": "first",
    }
    data = json.dumps(data)
    auth_header = create_authorization_header()

    update_response = client.post(
        "/service/{}/template/{}".format(service.id, template.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )

    assert update_response.status_code == 200
    update_json_resp = json.loads(update_response.get_data(as_text=True))
    assert update_json_resp["data"]["content"] == ("my template has new content, swell!")
    assert update_json_resp["data"]["postage"] == "first"
    assert update_json_resp["data"]["name"] == template.name
    assert update_json_resp["data"]["template_type"] == template.template_type
    assert update_json_resp["data"]["version"] == 2


def test_should_be_able_to_archive_template(client, sample_template):
    data = {
        "name": sample_template.name,
        "template_type": sample_template.template_type,
        "content": sample_template.content,
        "archived": True,
        "service": str(sample_template.service.id),
        "created_by": str(sample_template.created_by.id),
    }

    json_data = json.dumps(data)

    auth_header = create_authorization_header()

    resp = client.post(
        "/service/{}/template/{}".format(sample_template.service.id, sample_template.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=json_data,
    )

    assert resp.status_code == 200
    assert Template.query.first().archived


def test_should_be_able_to_archive_template_should_remove_template_folders(client, sample_service):
    template_folder = create_template_folder(service=sample_service)
    template = create_template(service=sample_service, folder=template_folder)

    data = {
        "archived": True,
    }

    client.post(
        f"/service/{sample_service.id}/template/{template.id}",
        headers=[("Content-Type", "application/json"), create_authorization_header()],
        data=json.dumps(data),
    )

    updated_template = Template.query.get(template.id)
    assert updated_template.archived
    assert not updated_template.folder


def test_get_precompiled_template_for_service(
    client,
    notify_user,
    sample_service,
):
    assert len(sample_service.templates) == 0

    response = client.get(
        "/service/{}/template/precompiled".format(sample_service.id),
        headers=[create_authorization_header()],
    )
    assert response.status_code == 200
    assert len(sample_service.templates) == 1

    data = json.loads(response.get_data(as_text=True))
    assert data["name"] == "Pre-compiled PDF"
    assert data["hidden"] is True


def test_get_precompiled_template_for_service_when_service_has_existing_precompiled_template(
    client,
    notify_user,
    sample_service,
):
    create_template(
        sample_service,
        template_name="Exisiting precompiled template",
        template_type=LETTER_TYPE,
        hidden=True,
    )
    assert len(sample_service.templates) == 1

    response = client.get(
        "/service/{}/template/precompiled".format(sample_service.id),
        headers=[create_authorization_header()],
    )

    assert response.status_code == 200
    assert len(sample_service.templates) == 1

    data = json.loads(response.get_data(as_text=True))
    assert data["name"] == "Exisiting precompiled template"
    assert data["hidden"] is True


def test_should_be_able_to_get_all_templates_for_a_service(client, sample_user, sample_service):
    data = {
        "name": "my template 1",
        "template_type": EMAIL_TYPE,
        "subject": "subject 1",
        "content": "template content",
        "service": str(sample_service.id),
        "created_by": str(sample_user.id),
    }
    data_1 = json.dumps(data)
    data = {
        "name": "my template 2",
        "template_type": EMAIL_TYPE,
        "subject": "subject 2",
        "content": "template content",
        "service": str(sample_service.id),
        "created_by": str(sample_user.id),
    }
    data_2 = json.dumps(data)
    auth_header = create_authorization_header()
    client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data_1,
    )
    auth_header = create_authorization_header()

    client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data_2,
    )

    auth_header = create_authorization_header()

    response = client.get("/service/{}/template".format(sample_service.id), headers=[auth_header])

    assert response.status_code == 200
    update_json_resp = json.loads(response.get_data(as_text=True))
    assert update_json_resp["data"][0]["name"] == "my template 1"
    assert update_json_resp["data"][0]["version"] == 1
    assert update_json_resp["data"][0]["created_at"]
    assert update_json_resp["data"][1]["name"] == "my template 2"
    assert update_json_resp["data"][1]["version"] == 1
    assert update_json_resp["data"][1]["created_at"]


def test_should_get_only_templates_for_that_service(admin_request, notify_db_session):
    service_1 = create_service(service_name="service_1")
    service_2 = create_service(service_name="service_2")
    id_1 = create_template(service_1).id
    id_2 = create_template(service_1).id
    id_3 = create_template(service_2).id

    json_resp_1 = admin_request.get("template.get_all_templates_for_service", service_id=service_1.id)
    json_resp_2 = admin_request.get("template.get_all_templates_for_service", service_id=service_2.id)

    assert {template["id"] for template in json_resp_1["data"]} == {
        str(id_1),
        str(id_2),
    }
    assert {template["id"] for template in json_resp_2["data"]} == {str(id_3)}


@pytest.mark.parametrize(
    "subject, content, template_type",
    [
        (
            "about your ((thing))",
            "hello ((name)) we’ve received your ((thing))",
            EMAIL_TYPE,
        ),
        (None, "hello ((name)) we’ve received your ((thing))", SMS_TYPE),
    ],
)
def test_should_get_a_single_template(notify_db, client, sample_user, service_factory, subject, content, template_type):
    template = create_sample_template(
        notify_db,
        notify_db.session,
        subject_line=subject,
        content=content,
        template_type=template_type,
    )

    response = client.get(
        "/service/{}/template/{}".format(template.service.id, template.id),
        headers=[create_authorization_header()],
    )

    data = json.loads(response.get_data(as_text=True))["data"]

    assert response.status_code == 200
    assert data["content"] == content
    assert data["subject"] == subject
    assert data["process_type"] == "normal"
    assert not data["redact_personalisation"]


@pytest.mark.parametrize(
    "subject, content, path, expected_subject, expected_content, expected_error",
    [
        (
            "about your thing",
            "hello user we’ve received your thing",
            "/service/{}/template/{}/preview",
            "about your thing",
            "hello user we’ve received your thing",
            None,
        ),
        (
            "about your ((thing))",
            "hello ((name)) we’ve received your ((thing))",
            "/service/{}/template/{}/preview?name=Amala&thing=document",
            "about your document",
            "hello Amala we’ve received your document",
            None,
        ),
        (
            "about your ((thing))",
            "hello ((name)) we’ve received your ((thing))",
            "/service/{}/template/{}/preview?eman=Amala&gniht=document",
            None,
            None,
            "Missing personalisation: thing, name",
        ),
        (
            "about your ((thing))",
            "hello ((name)) we’ve received your ((thing))",
            "/service/{}/template/{}/preview?name=Amala&thing=document&foo=bar",
            "about your document",
            "hello Amala we’ve received your document",
            None,
        ),
    ],
)
def test_should_preview_a_single_template(
    notify_db,
    client,
    sample_user,
    service_factory,
    subject,
    content,
    path,
    expected_subject,
    expected_content,
    expected_error,
):
    template = create_sample_template(
        notify_db,
        notify_db.session,
        subject_line=subject,
        content=content,
        template_type=EMAIL_TYPE,
    )

    response = client.get(
        path.format(template.service.id, template.id),
        headers=[create_authorization_header()],
    )

    content = json.loads(response.get_data(as_text=True))

    if expected_error:
        assert response.status_code == 400
        assert content["message"]["template"] == [expected_error]
    else:
        assert response.status_code == 200
        assert content["content"] == expected_content
        assert content["subject"] == expected_subject


def test_should_return_empty_array_if_no_templates_for_service(client, sample_service):
    auth_header = create_authorization_header()

    response = client.get("/service/{}/template".format(sample_service.id), headers=[auth_header])

    assert response.status_code == 200
    json_resp = json.loads(response.get_data(as_text=True))
    assert len(json_resp["data"]) == 0


def test_should_return_404_if_no_templates_for_service_with_id(client, sample_service, fake_uuid):
    auth_header = create_authorization_header()

    response = client.get(
        "/service/{}/template/{}".format(sample_service.id, fake_uuid),
        headers=[auth_header],
    )

    assert response.status_code == 404
    json_resp = json.loads(response.get_data(as_text=True))
    assert json_resp["result"] == "error"
    assert json_resp["message"] == "No result found"


@pytest.mark.parametrize(
    "template_type, char_count_limit", [(SMS_TYPE, TEMPLATE_NAME_CHAR_COUNT_LIMIT), (EMAIL_TYPE, TEMPLATE_NAME_CHAR_COUNT_LIMIT)]
)
def test_update_template_400_for_over_limit_name(
    client, mocker, sample_user, sample_service, sample_template, template_type, char_count_limit
):
    mocked_update_template = mocker.patch("app.dao.templates_dao.dao_update_template")
    name = "x" * (char_count_limit + 1)
    template_data = {
        "id": str(sample_template.id),
        "name": name,
        "template_type": template_type,
        "content": "some content here :)",
        "service": str(sample_service.id),
        "created_by": str(sample_user.id),
    }
    if template_type == EMAIL_TYPE:
        template_data.update({"subject": "subject"})
    request_data = json.dumps(template_data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template/{}".format(sample_service.id, sample_template.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=request_data,
    )
    assert response.status_code == 400
    json_response = json.loads(response.get_data(as_text=True))
    assert (f"Template name must be less than {char_count_limit} characters") in json_response["message"]["name"]
    mocked_update_template.assert_not_called()


@pytest.mark.parametrize(
    "template_type, char_count_limit", [(SMS_TYPE, TEMPLATE_NAME_CHAR_COUNT_LIMIT), (EMAIL_TYPE, TEMPLATE_NAME_CHAR_COUNT_LIMIT)]
)
def test_create_template_400_for_over_limit_name(client, mocker, sample_user, sample_service, template_type, char_count_limit):
    mocked_update_template = mocker.patch("app.dao.templates_dao.dao_create_template")
    name = "x" * (char_count_limit + 1)
    template_data = {
        "name": name,
        "template_type": template_type,
        "content": "some content here :)",
        "service": str(sample_service.id),
        "created_by": str(sample_user.id),
    }
    if template_type == EMAIL_TYPE:
        template_data.update({"subject": "subject"})
    request_data = json.dumps(template_data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=request_data,
    )
    assert response.status_code == 400
    json_response = json.loads(response.get_data(as_text=True))
    assert (f"Template name must be less than {char_count_limit} characters") in json_response["message"]["name"]
    mocked_update_template.assert_not_called()


@pytest.mark.parametrize(
    "template_type, char_count_limit",
    [
        (SMS_TYPE, SMS_CHAR_COUNT_LIMIT),
        (EMAIL_TYPE, EMAIL_CHAR_COUNT_LIMIT),
    ],
)
def test_create_400_for_over_limit_content(
    client, notify_api, sample_user, sample_service, fake_uuid, template_type, char_count_limit
):
    content = "x" * (char_count_limit + 1)
    data = {
        "name": "too big template",
        "template_type": template_type,
        "content": content,
        "service": str(sample_service.id),
        "created_by": str(sample_user.id),
    }

    if template_type == EMAIL_TYPE:
        data.update({"subject": "subject"})
    data = json.dumps(data)
    auth_header = create_authorization_header()

    response = client.post(
        "/service/{}/template".format(sample_service.id),
        headers=[("Content-Type", "application/json"), auth_header],
        data=data,
    )
    assert response.status_code == 400
    json_resp = json.loads(response.get_data(as_text=True))
    assert (f"Content has a character count greater than the limit of {char_count_limit}") in json_resp["message"]["content"]


@pytest.mark.parametrize(
    "template_type, char_count_limit",
    [
        (SMS_TYPE, SMS_CHAR_COUNT_LIMIT),
        (EMAIL_TYPE, EMAIL_CHAR_COUNT_LIMIT),
    ],
)
def test_update_400_for_over_limit_content(
    client, notify_db, notify_db_session, notify_api, sample_user, template_type, char_count_limit
):
    json_data = json.dumps(
        {
            "content": "x" * (char_count_limit + 1),
            "created_by": str(sample_user.id),
        }
    )
    auth_header = create_authorization_header()

    sample_template = create_sample_template(notify_db, notify_db_session, template_type=template_type)
    resp = client.post(
        f"/service/{sample_template.service.id}/template/{sample_template.id}",
        headers=[("Content-Type", "application/json"), auth_header],
        data=json_data,
    )
    assert resp.status_code == 400
    json_resp = json.loads(resp.get_data(as_text=True))
    assert (f"Content has a character count greater than the limit of {char_count_limit}") in json_resp["message"]["content"]


def test_should_return_all_template_versions_for_service_and_template_id(client, sample_template):
    original_content = sample_template.content
    from app.dao.templates_dao import dao_update_template

    sample_template.content = original_content + "1"
    dao_update_template(sample_template)
    sample_template.content = original_content + "2"
    dao_update_template(sample_template)

    auth_header = create_authorization_header()
    resp = client.get(
        "/service/{}/template/{}/versions".format(sample_template.service_id, sample_template.id),
        headers=[("Content-Type", "application/json"), auth_header],
    )
    assert resp.status_code == 200
    resp_json = json.loads(resp.get_data(as_text=True))["data"]
    assert len(resp_json) == 3
    for x in resp_json:
        if x["version"] == 1:
            assert x["content"] == original_content
        elif x["version"] == 2:
            assert x["content"] == original_content + "1"
        else:
            assert x["content"] == original_content + "2"


def test_update_does_not_create_new_version_when_there_is_no_change(client, sample_template):
    auth_header = create_authorization_header()
    data = {
        "template_type": sample_template.template_type,
        "content": sample_template.content,
    }
    resp = client.post(
        "/service/{}/template/{}".format(sample_template.service_id, sample_template.id),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )
    assert resp.status_code == 200

    template = dao_get_template_by_id(sample_template.id)
    assert template.version == 1


@pytest.mark.parametrize("process_type", ["priority", "bulk"])
def test_update_set_process_type_on_template(client, sample_template, process_type):
    auth_header = create_authorization_header()
    data = {"process_type": process_type}
    resp = client.post(
        "/service/{}/template/{}".format(sample_template.service_id, sample_template.id),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )
    assert resp.status_code == 200

    template = dao_get_template_by_id(sample_template.id)
    assert template.process_type == process_type


def test_create_a_template_with_reply_to(admin_request, sample_user):
    service = create_service(service_permissions=["letter"])
    letter_contact = create_letter_contact(service, "Edinburgh, ED1 1AA")
    data = {
        "name": "my template",
        "subject": "subject",
        "template_type": "letter",
        "content": "template <b>content</b>",
        "service": str(service.id),
        "created_by": str(sample_user.id),
        "reply_to": str(letter_contact.id),
    }

    json_resp = admin_request.post(
        "template.create_template",
        service_id=service.id,
        _data=data,
        _expected_status=201,
    )

    assert json_resp["data"]["template_type"] == "letter"
    assert json_resp["data"]["reply_to"] == str(letter_contact.id)
    assert json_resp["data"]["reply_to_text"] == letter_contact.contact_block

    template = Template.query.get(json_resp["data"]["id"])
    from app.schemas import template_schema

    assert sorted(json_resp["data"]) == sorted(template_schema.dump(template))
    th = TemplateHistory.query.filter_by(id=template.id, version=1).one()
    assert th.service_letter_contact_id == letter_contact.id


def test_create_a_template_with_foreign_service_reply_to(admin_request, sample_user):
    service = create_service(service_permissions=["letter"])
    service2 = create_service(
        service_name="test service",
        email_from="test@example.com",
        service_permissions=["letter"],
    )
    letter_contact = create_letter_contact(service2, "Edinburgh, ED1 1AA")
    data = {
        "name": "my template",
        "subject": "subject",
        "template_type": "letter",
        "content": "template <b>content</b>",
        "service": str(service.id),
        "created_by": str(sample_user.id),
        "reply_to": str(letter_contact.id),
    }

    json_resp = admin_request.post(
        "template.create_template",
        service_id=service.id,
        _data=data,
        _expected_status=400,
    )

    assert json_resp["message"] == "letter_contact_id {} does not exist in database for service id {}".format(
        str(letter_contact.id), str(service.id)
    )


@pytest.mark.parametrize(
    "template_default, service_default",
    [
        ("template address", "service address"),
        (None, "service address"),
        ("template address", None),
        (None, None),
    ],
)
def test_get_template_reply_to(client, sample_service, template_default, service_default):
    auth_header = create_authorization_header()
    if service_default:
        create_letter_contact(service=sample_service, contact_block=service_default, is_default=True)
    if template_default:
        template_default_contact = create_letter_contact(service=sample_service, contact_block=template_default, is_default=False)
    reply_to_id = str(template_default_contact.id) if template_default else None
    template = create_template(service=sample_service, template_type="letter", reply_to=reply_to_id)

    resp = client.get(
        "/service/{}/template/{}".format(template.service_id, template.id),
        headers=[auth_header],
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    json_resp = json.loads(resp.get_data(as_text=True))

    assert "service_letter_contact_id" not in json_resp["data"]
    assert json_resp["data"]["reply_to"] == reply_to_id
    assert json_resp["data"]["reply_to_text"] == template_default


def test_update_template_reply_to(client, sample_letter_template):
    auth_header = create_authorization_header()
    letter_contact = create_letter_contact(sample_letter_template.service, "Edinburgh, ED1 1AA")
    data = {
        "reply_to": str(letter_contact.id),
    }

    resp = client.post(
        "/service/{}/template/{}".format(sample_letter_template.service_id, sample_letter_template.id),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)

    template = dao_get_template_by_id(sample_letter_template.id)
    assert template.service_letter_contact_id == letter_contact.id
    th = TemplateHistory.query.filter_by(id=sample_letter_template.id, version=2).one()
    assert th.service_letter_contact_id == letter_contact.id


def test_update_template_reply_to_set_to_blank(client, notify_db_session):
    auth_header = create_authorization_header()
    service = create_service(service_permissions=["letter"])
    letter_contact = create_letter_contact(service, "Edinburgh, ED1 1AA")
    template = create_template(service=service, template_type="letter", reply_to=letter_contact.id)

    data = {
        "reply_to": None,
    }

    resp = client.post(
        "/service/{}/template/{}".format(template.service_id, template.id),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)

    template = dao_get_template_by_id(template.id)
    assert template.service_letter_contact_id is None
    th = TemplateHistory.query.filter_by(id=template.id, version=2).one()
    assert th.service_letter_contact_id is None


def test_update_template_with_foreign_service_reply_to(client, sample_letter_template):
    auth_header = create_authorization_header()

    service2 = create_service(
        service_name="test service",
        email_from="test@example.com",
        service_permissions=["letter"],
    )
    letter_contact = create_letter_contact(service2, "Edinburgh, ED1 1AA")

    data = {
        "reply_to": str(letter_contact.id),
    }

    resp = client.post(
        "/service/{}/template/{}".format(sample_letter_template.service_id, sample_letter_template.id),
        data=json.dumps(data),
        headers=[("Content-Type", "application/json"), auth_header],
    )

    assert resp.status_code == 400, resp.get_data(as_text=True)
    json_resp = json.loads(resp.get_data(as_text=True))

    assert json_resp["message"] == "letter_contact_id {} does not exist in database for service id {}".format(
        str(letter_contact.id), str(sample_letter_template.service_id)
    )


def test_update_redact_template(admin_request, sample_template):
    assert sample_template.redact_personalisation is False

    data = {
        "redact_personalisation": True,
        "created_by": str(sample_template.created_by_id),
    }

    dt = datetime.now()

    with freeze_time(dt):
        resp = admin_request.post(
            "template.update_template",
            service_id=sample_template.service_id,
            template_id=sample_template.id,
            _data=data,
        )

    assert resp is None

    assert sample_template.redact_personalisation is True
    assert sample_template.template_redacted.updated_by_id == sample_template.created_by_id
    assert sample_template.template_redacted.updated_at == dt

    assert sample_template.version == 1


def test_update_redact_template_ignores_other_properties(admin_request, sample_template):
    data = {
        "name": "Foo",
        "redact_personalisation": True,
        "created_by": str(sample_template.created_by_id),
    }

    admin_request.post(
        "template.update_template",
        service_id=sample_template.service_id,
        template_id=sample_template.id,
        _data=data,
    )

    assert sample_template.redact_personalisation is True
    assert sample_template.name != "Foo"


def test_update_redact_template_does_nothing_if_already_redacted(admin_request, sample_template):
    dt = datetime.now()
    with freeze_time(dt):
        dao_redact_template(sample_template, sample_template.created_by_id)

    data = {
        "redact_personalisation": True,
        "created_by": str(sample_template.created_by_id),
    }

    with freeze_time(dt + timedelta(days=1)):
        resp = admin_request.post(
            "template.update_template",
            service_id=sample_template.service_id,
            template_id=sample_template.id,
            _data=data,
        )

    assert resp is None

    assert sample_template.redact_personalisation is True
    # make sure that it hasn't been updated
    assert sample_template.template_redacted.updated_at == dt


def test_update_redact_template_400s_if_no_created_by(admin_request, sample_template):
    original_updated_time = sample_template.template_redacted.updated_at
    resp = admin_request.post(
        "template.update_template",
        service_id=sample_template.service_id,
        template_id=sample_template.id,
        _data={"redact_personalisation": True},
        _expected_status=400,
    )

    assert resp == {"result": "error", "message": {"created_by": ["Field is required"]}}

    assert sample_template.redact_personalisation is False
    assert sample_template.template_redacted.updated_at == original_updated_time


def test_preview_letter_template_by_id_invalid_file_type(sample_letter_notification, admin_request):
    resp = admin_request.get(
        "template.preview_letter_template_by_notification_id",
        service_id=sample_letter_notification.service_id,
        template_id=sample_letter_notification.template_id,
        notification_id=sample_letter_notification.id,
        file_type="doc",
        _expected_status=400,
    )

    assert ["file_type must be pdf or png"] == resp["message"]["content"]


@freeze_time("2012-12-12")
@pytest.mark.parametrize("file_type", ("png", "pdf"))
def test_preview_letter_template_by_id_valid_file_type(
    notify_api,
    sample_letter_notification,
    admin_request,
    file_type,
):
    sample_letter_notification.created_at = datetime.utcnow()
    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker() as request_mock:
            content = b"\x00\x01"

            mock_post = request_mock.post(
                "http://localhost/notifications-template-preview/preview.{}".format(file_type),
                content=content,
                headers={"X-pdf-page-count": "1"},
                status_code=200,
            )

            resp = admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=sample_letter_notification.service_id,
                notification_id=sample_letter_notification.id,
                file_type=file_type,
            )

            post_json = mock_post.last_request.json()
            assert post_json["template"]["id"] == str(sample_letter_notification.template_id)
            assert post_json["values"] == {
                "address_line_1": "A1",
                "address_line_2": "A2",
                "address_line_3": "A3",
                "address_line_4": "A4",
                "address_line_5": "A5",
                "address_line_6": "A6",
                "postcode": "A_POST",
            }
            assert post_json["date"] == "2012-12-12T00:00:00"
            assert post_json["filename"] is None
            assert base64.b64decode(resp["content"]) == content


def test_preview_letter_template_by_id_template_preview_500(notify_api, client, admin_request, sample_letter_notification):
    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        import requests_mock

        with requests_mock.Mocker() as request_mock:
            content = b"\x00\x01"

            mock_post = request_mock.post(
                "http://localhost/notifications-template-preview/preview.pdf",
                content=content,
                headers={"X-pdf-page-count": "1"},
                status_code=404,
            )

            resp = admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=sample_letter_notification.service_id,
                notification_id=sample_letter_notification.id,
                file_type="pdf",
                _expected_status=500,
            )

            assert mock_post.last_request.json()
            assert "Status code: 404" in resp["message"]
            assert "Error generating preview letter for {}".format(sample_letter_notification.id) in resp["message"]


def test_preview_letter_template_precompiled_pdf_file_type(notify_api, client, admin_request, sample_service, mocker):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker():
            content = b"\x00\x01"

            mock_get_letter_pdf = mocker.patch("app.template.rest.get_letter_pdf", return_value=content)

            resp = admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=notification.service_id,
                notification_id=notification.id,
                file_type="pdf",
            )

            mock_get_letter_pdf.assert_called_once_with(notification)
            assert base64.b64decode(resp["content"]) == content


def test_preview_letter_template_precompiled_s3_error(notify_api, client, admin_request, sample_service, mocker):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker():
            mocker.patch(
                "app.template.rest.get_letter_pdf",
                side_effect=botocore.exceptions.ClientError({"Error": {"Code": "403", "Message": "Unauthorized"}}, "GetObject"),
            )

            request = admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=notification.service_id,
                notification_id=notification.id,
                file_type="pdf",
                _expected_status=500,
            )

            assert (
                request["message"]
                == "Error extracting requested page from PDF file for notification_id {} type "
                "<class 'botocore.exceptions.ClientError'> An error occurred (403) "
                "when calling the GetObject operation: Unauthorized".format(notification.id)
            )


@pytest.mark.parametrize(
    "filetype, post_url, overlay",
    [
        ("png", "precompiled-preview.png", None),
        ("png", "precompiled/overlay.png?page_number=1", 1),
        ("pdf", "precompiled/overlay.pdf", 1),
    ],
)
def test_preview_letter_template_precompiled_png_file_type_or_pdf_with_overlay(
    notify_api,
    client,
    admin_request,
    sample_service,
    mocker,
    filetype,
    post_url,
    overlay,
):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker() as request_mock:
            pdf_content = b"\x00\x01"
            expected_returned_content = b"\x00\x02"

            mock_get_letter_pdf = mocker.patch("app.template.rest.get_letter_pdf", return_value=pdf_content)

            mocker.patch("app.template.rest.extract_page_from_pdf", return_value=pdf_content)

            mock_post = request_mock.post(
                "http://localhost/notifications-template-preview/{}".format(post_url),
                content=expected_returned_content,
                headers={"X-pdf-page-count": "1"},
                status_code=200,
            )

            resp = admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=notification.service_id,
                notification_id=notification.id,
                file_type=filetype,
                overlay=overlay,
            )

            with pytest.raises(ValueError):
                mock_post.last_request.json()
            mock_get_letter_pdf.assert_called_once_with(notification)
            assert base64.b64decode(resp["content"]) == expected_returned_content


@pytest.mark.parametrize(
    "page_number,expect_preview_url",
    [
        (
            "",
            "http://localhost/notifications-template-preview/precompiled-preview.png?hide_notify=true",
        ),
        (
            "1",
            "http://localhost/notifications-template-preview/precompiled-preview.png?hide_notify=true",
        ),
        (
            "2",
            "http://localhost/notifications-template-preview/precompiled-preview.png",
        ),
    ],
)
def test_preview_letter_template_precompiled_png_file_type_hide_notify_tag_only_on_first_page(
    notify_api,
    client,
    admin_request,
    sample_service,
    mocker,
    page_number,
    expect_preview_url,
):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        pdf_content = b"\x00\x01"
        png_content = b"\x00\x02"
        encoded = base64.b64encode(png_content).decode("utf-8")

        mocker.patch("app.template.rest.get_letter_pdf", return_value=pdf_content)
        mocker.patch("app.template.rest.extract_page_from_pdf", return_value=png_content)
        mock_get_png_preview = mocker.patch("app.template.rest._get_png_preview_or_overlaid_pdf", return_value=encoded)

        admin_request.get(
            "template.preview_letter_template_by_notification_id",
            service_id=notification.service_id,
            notification_id=notification.id,
            file_type="png",
            page=page_number,
        )

        mock_get_png_preview.assert_called_once_with(expect_preview_url, encoded, notification.id, json=False)


def test_preview_letter_template_precompiled_png_template_preview_500_error(
    notify_api, client, admin_request, sample_service, mocker
):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker() as request_mock:
            pdf_content = b"\x00\x01"
            png_content = b"\x00\x02"

            mocker.patch("app.template.rest.get_letter_pdf", return_value=pdf_content)

            mocker.patch("app.template.rest.extract_page_from_pdf", return_value=pdf_content)

            mock_post = request_mock.post(
                "http://localhost/notifications-template-preview/precompiled-preview.png",
                content=png_content,
                headers={"X-pdf-page-count": "1"},
                status_code=500,
            )

            admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=notification.service_id,
                notification_id=notification.id,
                file_type="png",
                _expected_status=500,
            )

            with pytest.raises(ValueError):
                mock_post.last_request.json()


def test_preview_letter_template_precompiled_png_template_preview_400_error(
    notify_api, client, admin_request, sample_service, mocker
):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker() as request_mock:
            pdf_content = b"\x00\x01"
            png_content = b"\x00\x02"

            mocker.patch("app.template.rest.get_letter_pdf", return_value=pdf_content)

            mocker.patch("app.template.rest.extract_page_from_pdf", return_value=pdf_content)

            mock_post = request_mock.post(
                "http://localhost/notifications-template-preview/precompiled-preview.png",
                content=png_content,
                headers={"X-pdf-page-count": "1"},
                status_code=404,
            )

            admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=notification.service_id,
                notification_id=notification.id,
                file_type="png",
                _expected_status=500,
            )

            with pytest.raises(ValueError):
                mock_post.last_request.json()


def test_preview_letter_template_precompiled_png_template_preview_pdf_error(
    notify_api, client, admin_request, sample_service, mocker
):
    template = create_template(
        sample_service,
        template_type="letter",
        template_name="Pre-compiled PDF",
        subject="Pre-compiled PDF",
        hidden=True,
    )

    notification = save_notification(create_notification(template))

    with set_config_values(
        notify_api,
        {
            "TEMPLATE_PREVIEW_API_HOST": "http://localhost/notifications-template-preview",
            "TEMPLATE_PREVIEW_API_KEY": "test-key",
        },
    ):
        with requests_mock.Mocker() as request_mock:
            pdf_content = b"\x00\x01"
            png_content = b"\x00\x02"

            mocker.patch("app.template.rest.get_letter_pdf", return_value=pdf_content)

            error_message = "PDF Error message"
            mocker.patch(
                "app.template.rest.extract_page_from_pdf",
                side_effect=PdfReadError(error_message),
            )

            request_mock.post(
                "http://localhost/notifications-template-preview/precompiled-preview.png",
                content=png_content,
                headers={"X-pdf-page-count": "1"},
                status_code=404,
            )

            request = admin_request.get(
                "template.preview_letter_template_by_notification_id",
                service_id=notification.service_id,
                notification_id=notification.id,
                file_type="png",
                _expected_status=500,
            )

            assert request[
                "message"
            ] == "Error extracting requested page from PDF file for notification_id {} type " "{} {}".format(
                notification.id, type(PdfReadError()), error_message
            )


def test_should_template_be_redacted():
    some_org = create_organisation()
    assert not should_template_be_redacted(some_org)

    dao_update_organisation(some_org.id, organisation_type="province_or_territory")
    assert should_template_be_redacted(some_org)


def test_update_templates_category(sample_template, sample_template_category, admin_request):
    admin_request.post(
        "template.update_templates_category",
        service_id=sample_template.service_id,
        template_id=sample_template.id,
        template_category_id=sample_template_category.id,
        _expected_status=200,
    )

    template = dao_get_template_by_id(sample_template.id)

    assert template.template_category.id == sample_template_category.id


class TestTemplateCategory:
    DEFAULT_TEMPLATE_CATEGORY_LOW = "0dda24c2-982a-4f44-9749-0e38b2607e89"
    DEFAULT_TEMPLATE_CATEGORY_MEDIUM = "f75d6706-21b7-437e-b93a-2c0ab771e28e"

    # ensure that the process_type is overridden when a user changes categories
    @pytest.mark.parametrize(
        "template_category_id, data_process_type, expected_process_type_column, expected_process_type",
        [
            # category doesnt change, process_type should remain as priority
            (
                "unchanged",
                "priority",
                "priority",
                "priority",
            ),
            # category changes, process_type should be removed
            (DEFAULT_TEMPLATE_CATEGORY_MEDIUM, None, None, "normal"),
        ],
    )
    def test_process_type_should_be_reset_when_template_category_updated(
        self,
        sample_service,
        sample_template_with_priority_override,
        sample_user,
        admin_request,
        populate_generic_categories,
        template_category_id,
        data_process_type,
        expected_process_type_column,
        expected_process_type,
        notify_api,
    ):
        template_orig = dao_get_template_by_id(sample_template_with_priority_override.id)

        calculated_tc = template_category_id if template_category_id != "unchanged" else str(template_orig.template_category_id)
        admin_request.post(
            "template.update_template",
            service_id=sample_template_with_priority_override.service_id,
            template_id=sample_template_with_priority_override.id,
            _data={
                "template_category_id": calculated_tc,
                "redact_personalisation": False,
                "process_type": data_process_type,
            },
            _expected_status=200,
        )
        template = dao_get_template_by_id(sample_template_with_priority_override.id)

        assert str(template.template_category_id) == calculated_tc
        assert template.process_type_column == expected_process_type_column
        assert template.process_type == expected_process_type

    @pytest.mark.parametrize(
        "template_type, initial_process_type, updated_process_type",
        [
            (SMS_TYPE, None, "bulk"),
            (EMAIL_TYPE, None, "bulk"),
            (SMS_TYPE, None, "normal"),
            (EMAIL_TYPE, None, "normal"),
            (SMS_TYPE, None, "priority"),
            (EMAIL_TYPE, None, "priority"),
            (SMS_TYPE, "bulk", "bulk"),
            (EMAIL_TYPE, "bulk", "bulk"),
            (SMS_TYPE, "bulk", "normal"),
            (EMAIL_TYPE, "bulk", "normal"),
            (SMS_TYPE, "bulk", "priority"),
            (EMAIL_TYPE, "bulk", "priority"),
        ],
    )
    def test_update_template_override_process_type_ff_on(
        self,
        admin_request,
        sample_user,
        notify_api,
        sample_template_category,
        template_type,
        initial_process_type,
        updated_process_type,
    ):
        service = create_service(service_name="service_1")
        template = create_template(
            service,
            template_type=template_type,
            template_name="testing template",
            subject="Template subject",
            content="Dear Sir/Madam, Hello. Yours Truly, The Government.",
            template_category=sample_template_category,
            process_type=initial_process_type,
        )

        template_data = {
            "id": str(template.id),
            "name": "new name",
            "template_type": template_type,
            "content": "some content here :)",
            "service": str(service.id),
            "created_by": str(sample_user.id),
            "template_category_id": str(sample_template_category.id),
            "process_type": updated_process_type,
        }

        response = admin_request.post(
            "template.update_template",
            service_id=service.id,
            template_id=template.id,
            _data=template_data,
            _expected_status=200,
        )
        assert response["data"]["process_type"] == updated_process_type
        assert response["data"]["template_category"]["id"] == str(sample_template_category.id)

    @pytest.mark.parametrize(
        "template_type, process_type, template_category",
        [
            (SMS_TYPE, None, "bulk"),
            (EMAIL_TYPE, None, "bulk"),
            (SMS_TYPE, None, "normal"),
            (EMAIL_TYPE, None, "normal"),
            (SMS_TYPE, None, "priority"),
            (EMAIL_TYPE, None, "priority"),
            (SMS_TYPE, "bulk", "bulk"),
            (EMAIL_TYPE, "bulk", "bulk"),
            (SMS_TYPE, "bulk", "normal"),
            (EMAIL_TYPE, "bulk", "normal"),
            (SMS_TYPE, "bulk", "priority"),
            (EMAIL_TYPE, "bulk", "priority"),
            (SMS_TYPE, "normal", "bulk"),
            (EMAIL_TYPE, "normal", "bulk"),
            (SMS_TYPE, "normal", "normal"),
            (EMAIL_TYPE, "normal", "normal"),
            (SMS_TYPE, "normal", "priority"),
            (EMAIL_TYPE, "normal", "priority"),
            (SMS_TYPE, "priority", "bulk"),
            (EMAIL_TYPE, "priority", "bulk"),
            (SMS_TYPE, "priority", "normal"),
            (EMAIL_TYPE, "priority", "normal"),
            (SMS_TYPE, "priority", "priority"),
            (EMAIL_TYPE, "priority", "priority"),
        ],
    )
    def test_update_template_change_category_ff_on(
        self,
        admin_request,
        sample_user,
        notify_api,
        sample_template_category,
        template_type,
        process_type,
        template_category,
        sample_template_category_priority,
        sample_template_category_bulk,
    ):
        service = create_service(service_name="service_1")
        template = create_template(
            service,
            template_type=template_type,
            template_name="testing template",
            subject="Template subject",
            content="Dear Sir/Madam, Hello. Yours Truly, The Government.",
            template_category=sample_template_category,
            process_type=process_type,
        )

        tc = sample_template_category
        if template_category == "normal":
            tc = sample_template_category
        elif template_category == "bulk":
            tc = sample_template_category_bulk
        elif template_category == "priority":
            tc = sample_template_category_priority

        template_data = {
            "name": "new name",
            "template_type": template_type,
            "content": "some content here :)",
            "subject": "yo",
            "service": str(service.id),
            "created_by": str(sample_user.id),
            "template_category_id": str(tc.id),
            "process_type": process_type,
        }

        response = admin_request.post(
            "template.update_template",
            service_id=service.id,
            template_id=template.id,
            _data=template_data,
            _expected_status=200,
        )

        assert response["data"]["process_type_column"] == process_type
        assert response["data"]["process_type"] == template_category if process_type is None else process_type
        assert response["data"]["template_category_id"] == str(tc.id)

    @pytest.mark.parametrize(
        "template_type, process_type, calculated_process_type",
        [
            (SMS_TYPE, "bulk", "bulk"),
            (EMAIL_TYPE, "bulk", "bulk"),
            (SMS_TYPE, "normal", "normal"),
            (EMAIL_TYPE, "normal", "normal"),
            (SMS_TYPE, "priority", "priority"),
            (EMAIL_TYPE, "priority", "priority"),
            (SMS_TYPE, None, "bulk"),
            (EMAIL_TYPE, None, "bulk"),
            (SMS_TYPE, None, "normal"),
            (EMAIL_TYPE, None, "normal"),
            (SMS_TYPE, None, "priority"),
            (EMAIL_TYPE, None, "priority"),
        ],
    )
    def test_create_template_with_category_ff_on(
        self,
        admin_request,
        sample_user,
        notify_api,
        sample_template_category,
        template_type,
        process_type,
        calculated_process_type,
        sample_template_category_priority,
        sample_template_category_bulk,
    ):
        service = create_service(service_name="service_1")

        tc = sample_template_category
        if process_type is None:
            if calculated_process_type == "normal":
                tc = sample_template_category
            elif calculated_process_type == "bulk":
                tc = sample_template_category_bulk
            elif calculated_process_type == "priority":
                tc = sample_template_category_priority
        else:
            tc = sample_template_category

        template_data = {
            "name": "new name",
            "template_type": template_type,
            "content": "some content here :)",
            "subject": "yo",
            "service": str(service.id),
            "created_by": str(sample_user.id),
            "template_category_id": str(tc.id),
            "process_type": process_type,
        }

        response = admin_request.post(
            "template.create_template", service_id=service.id, _data=template_data, _expected_status=201
        )

        assert response["data"]["process_type_column"] == process_type
        assert response["data"]["process_type"] == calculated_process_type
        assert response["data"]["template_category_id"] == str(tc.id)


@pytest.mark.parametrize(
    "original_text_direction, updated_text_direction, expected_original, expected_after_update",
    [
        (None, True, False, True),
        (True, False, True, False),
    ],
)
def test_template_updated_when_rtl_changes(
    admin_request,
    sample_user,
    notify_api,
    sample_template_category,
    original_text_direction,
    updated_text_direction,
    expected_original,
    expected_after_update,
):
    service = create_service(service_name="service_1")
    template_data = {
        "service": service,
        "template_type": "email",
        "template_name": "testing template",
        "subject": "Template subject",
        "content": "Dear Sir/Madam, Hello. Yours Truly, The Government.",
        "template_category": sample_template_category,
        "process_type": "normal",
        "text_direction_rtl": original_text_direction,
    }
    template = create_template(**template_data)

    assert template.text_direction_rtl is expected_original

    # change the RTL property
    updated_template_data = {
        "service": str(service.id),
        "template_category": str(sample_template_category.id),
        "template_type": template_data["template_type"],
        "template_name": template_data["template_name"],
        "subject": template_data["subject"],
        "content": template_data["content"],
        "process_type": template_data["process_type"],
    }

    if updated_text_direction is not None:
        updated_template_data["text_direction_rtl"] = updated_text_direction

    response = admin_request.post(
        "template.update_template",
        service_id=service.id,
        template_id=template.id,
        _data=updated_template_data,
        _expected_status=200,
    )

    assert response["data"]["text_direction_rtl"] == expected_after_update


@pytest.mark.parametrize("text_direction, expected_text_direction", [(True, True), (False, False), (None, False)])
def test_template_can_be_created_with_text_direction(
    admin_request, sample_user, sample_template_category, text_direction, expected_text_direction
):
    service = create_service(service_name="service_1")

    template_data = {
        "name": "new name",
        "template_type": "email",
        "content": "some content here :)",
        "subject": "yo",
        "service": str(service.id),
        "created_by": str(sample_user.id),
        "template_category_id": str(sample_template_category.id),
        "process_type": "normal",
    }

    if text_direction is not None:
        template_data["text_direction_rtl"] = text_direction

    response = admin_request.post("template.create_template", service_id=service.id, _data=template_data, _expected_status=201)

    assert response["data"]["text_direction_rtl"] == expected_text_direction
