import uuid
from collections import namedtuple
from datetime import datetime
from unittest.mock import ANY, MagicMock, call

import pytest
from flask import current_app
from notifications_utils.recipients import validate_and_format_phone_number
from pytest_mock import MockerFixture
from requests import HTTPError

import app
from app import aws_sns_client
from app.dao import notifications_dao, provider_details_dao
from app.dao.provider_details_dao import (
    dao_switch_sms_provider_to_provider_with_identifier,
)
from app.delivery import send_to_providers
from app.exceptions import (
    InvalidUrlException,
    MalwarePendingException,
    NotificationTechnicalFailureException,
)
from app.models import (
    BRANDING_BOTH_EN,
    BRANDING_BOTH_FR,
    BRANDING_ORG_BANNER_NEW,
    BRANDING_ORG_NEW,
    KEY_TYPE_NORMAL,
    KEY_TYPE_TEAM,
    KEY_TYPE_TEST,
    EmailBranding,
    Notification,
    Service,
)
from tests.app.conftest import create_sample_email_template, document_download_response
from tests.app.db import (
    create_notification,
    create_reply_to_email,
    create_service,
    create_service_sms_sender,
    create_service_with_defined_sms_sender,
    create_template,
    save_notification,
)
from tests.conftest import set_config_values


@pytest.mark.skip(reason="Currently using only 1 SMS provider")
def test_should_return_highest_priority_active_provider(restore_provider_details):
    providers = provider_details_dao.get_provider_details_by_notification_type("sms")
    providers = [provider for provider in providers if provider.active]

    first = providers[0]
    second = providers[1]

    assert send_to_providers.provider_to_use("sms", "1234").name == first.identifier

    first.priority = 12
    second.priority = 10

    provider_details_dao.dao_update_provider_details(first)
    provider_details_dao.dao_update_provider_details(second)

    assert send_to_providers.provider_to_use("sms", "1234").name == second.identifier

    first.priority = 10
    first.active = False
    second.priority = 12

    provider_details_dao.dao_update_provider_details(first)
    provider_details_dao.dao_update_provider_details(second)

    assert send_to_providers.provider_to_use("sms", "1234").name == second.identifier

    first.active = True
    provider_details_dao.dao_update_provider_details(first)

    assert send_to_providers.provider_to_use("sms", "1234").name == first.identifier


def test_provider_to_use(restore_provider_details):
    providers = provider_details_dao.get_provider_details_by_notification_type("sms")
    first = providers[0]

    assert first.identifier == "sns"

    # provider is still SNS if SMS and sender is set
    provider = send_to_providers.provider_to_use("sms", "1234", False, "+12345678901")
    assert first.identifier == provider.name

    # provider is highest priority sms provider if sender is not set
    provider = send_to_providers.provider_to_use("sms", "1234", False)
    assert first.identifier == provider.name


def test_should_send_personalised_template_to_correct_sms_provider_and_persist(sample_sms_template_with_html, mocker):
    db_notification = save_notification(
        create_notification(
            template=sample_sms_template_with_html,
            to_field="+16502532222",
            personalisation={"name": "Jo"},
            status="created",
            reply_to_text=sample_sms_template_with_html.service.get_default_sms_sender(),
        )
    )

    statsd_mock = mocker.patch("app.delivery.send_to_providers.statsd_client")
    mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")

    send_to_providers.send_sms_to_provider(db_notification)

    aws_sns_client.send_sms.assert_called_once_with(
        to=validate_and_format_phone_number("+16502532222"),
        content="Sample service: Hello Jo\nHere is <em>some HTML</em> & entities",
        reference=str(db_notification.id),
        sender=current_app.config["FROM_NUMBER"],
    )

    notification = Notification.query.filter_by(id=db_notification.id).one()

    assert notification.status == "sent"
    assert notification.sent_at <= datetime.utcnow()
    assert notification.sent_by == "sns"
    assert notification.billable_units == 1
    assert notification.personalisation == {"name": "Jo"}
    assert notification.reference == "message_id_from_sns"

    statsd_timing_calls = statsd_mock.timing_with_dates.call_args_list

    assert call("sms.total-time", notification.sent_at, notification.created_at) in statsd_timing_calls
    assert call("sms.process_type-normal", notification.sent_at, notification.created_at) in statsd_timing_calls
    assert call("sms.process_type-normal") in statsd_mock.incr.call_args_list


def test_should_send_personalised_template_to_correct_email_provider_and_persist(sample_email_template_with_html, mocker):
    db_notification = save_notification(
        create_notification(
            template=sample_email_template_with_html,
            to_field="jo.smith@example.com",
            personalisation={"name": "Jo"},
        )
    )

    mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    statsd_mock = mocker.patch("app.delivery.send_to_providers.statsd_client")

    send_to_providers.send_email_to_provider(db_notification)

    app.aws_ses_client.send_email.assert_called_once_with(
        '"Sample service" <sample.service@notification.canada.ca>',
        "jo.smith@example.com",
        "Jo <em>some HTML</em>",
        body="Hello Jo\nThis is an email from GOV.\u200bUK with <em>some HTML</em>\n",
        html_body=ANY,
        reply_to_address=None,
        attachments=[],
    )

    assert "<!DOCTYPE html" in app.aws_ses_client.send_email.call_args[1]["html_body"]
    assert "&lt;em&gt;some HTML&lt;/em&gt;" in app.aws_ses_client.send_email.call_args[1]["html_body"]

    notification = Notification.query.filter_by(id=db_notification.id).one()
    assert notification.status == "sending"
    assert notification.sent_at <= datetime.utcnow()
    assert notification.sent_by == "ses"
    assert notification.personalisation == {"name": "Jo"}

    statsd_timing_calls = statsd_mock.timing_with_dates.call_args_list
    statsd_key = "email.no-attachments.process_type-normal"
    assert call("email.total-time", notification.sent_at, notification.created_at) in statsd_timing_calls
    assert call(statsd_key, notification.sent_at, notification.created_at) in statsd_timing_calls
    assert call(statsd_key) in statsd_mock.incr.call_args_list


@pytest.mark.skip(reason="the validator can throw a 500 causing us to fail all tests")
def test_should_send_personalised_template_with_html_enabled(sample_email_template_with_advanced_html, mocker, notify_api):
    db_notification = save_notification(
        create_notification(
            template=sample_email_template_with_advanced_html,
            to_field="jo.smith@example.com",
            personalisation={"name": "Jo"},
        )
    )

    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    with set_config_values(
        notify_api,
        {
            "ALLOW_HTML_SERVICE_IDS": str(db_notification.service.id),
        },
    ):
        send_to_providers.send_email_to_provider(db_notification)

    app.aws_ses_client.send_email.assert_called_once_with(
        '"Sample service" <sample.service@notification.canada.ca>',
        "jo.smith@example.com",
        "Jo <em>some HTML</em>",
        body="<div style='color: pink' dir='rtl'>Jo <em>some HTML</em> that should be right aligned</div>\n",
        html_body=ANY,
        reply_to_address=None,
        attachments=[],
    )

    assert "<!DOCTYPE html" in app.aws_ses_client.send_email.call_args[1]["html_body"]
    assert (
        "<div style='color: pink' dir='rtl'>Jo <em>some HTML</em> that should be right aligned</div>"
        in app.aws_ses_client.send_email.call_args[1]["html_body"]
    )


def test_should_not_send_email_message_when_service_is_inactive_notifcation_is_in_tech_failure(
    sample_service, sample_notification, mocker
):
    sample_service.active = False
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    with pytest.raises(NotificationTechnicalFailureException) as e:
        send_to_providers.send_email_to_provider(sample_notification)
    assert str(sample_notification.id) in str(e.value)
    send_mock.assert_not_called()
    assert Notification.query.get(sample_notification.id).status == "technical-failure"


def test_should_respect_custom_sending_domains(sample_service, mocker, sample_email_template_with_html):
    db_notification = save_notification(
        create_notification(
            template=sample_email_template_with_html,
            to_field="jo.smith@example.com",
            personalisation={"name": "Jo"},
        )
    )

    sample_service.sending_domain = "foo.bar"
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    send_to_providers.send_email_to_provider(db_notification)

    app.aws_ses_client.send_email.assert_called_once_with(
        '"Sample service" <sample.service@foo.bar>',
        "jo.smith@example.com",
        "Jo <em>some HTML</em>",
        body="Hello Jo\nThis is an email from GOV.\u200bUK with <em>some HTML</em>\n",
        html_body=ANY,
        reply_to_address=None,
        attachments=[],
    )


@pytest.mark.parametrize("client_send", ["app.aws_sns_client.send_sms"])
def test_should_not_send_sms_message_when_service_is_inactive_notifcation_is_in_tech_failure(
    sample_service, sample_notification, mocker, client_send
):
    sample_service.active = False
    send_mock = mocker.patch(client_send, return_value="reference")

    with pytest.raises(NotificationTechnicalFailureException) as e:
        send_to_providers.send_sms_to_provider(sample_notification)
    assert str(sample_notification.id) in str(e.value)
    send_mock.assert_not_called()
    assert Notification.query.get(sample_notification.id).status == "technical-failure"


@pytest.mark.parametrize("var", ["", " "])
def test_should_not_send_sms_message_when_message_is_empty_or_whitespace(sample_service, mocker, var):
    sample_service.prefix_sms = False
    template = create_template(sample_service, content="((var))")
    notification = save_notification(
        create_notification(
            template=template,
            personalisation={"var": var},
            to_field="+16502532222",
            status="created",
            reply_to_text=sample_service.get_default_sms_sender(),
        )
    )

    send_mock = mocker.patch("app.aws_sns_client.send_sms", return_value="reference")

    send_to_providers.send_sms_to_provider(notification)

    send_mock.assert_not_called()
    assert Notification.query.get(notification.id).status == "technical-failure"


def test_send_sms_should_use_template_version_from_notification_not_latest(sample_template, mocker):
    db_notification = save_notification(
        create_notification(
            template=sample_template,
            to_field="+16502532222",
            status="created",
            reply_to_text=sample_template.service.get_default_sms_sender(),
        )
    )

    mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")

    version_on_notification = sample_template.version

    # Change the template
    from app.dao.templates_dao import dao_get_template_by_id, dao_update_template

    sample_template.content = sample_template.content + " another version of the template"
    dao_update_template(sample_template)
    t = dao_get_template_by_id(sample_template.id)
    assert t.version > version_on_notification

    send_to_providers.send_sms_to_provider(db_notification)

    aws_sns_client.send_sms.assert_called_once_with(
        to=validate_and_format_phone_number("+16502532222"),
        content="Sample service: This is a template:\nwith a newline",
        reference=str(db_notification.id),
        sender=current_app.config["FROM_NUMBER"],
    )

    persisted_notification = notifications_dao.get_notification_by_id(db_notification.id)
    assert persisted_notification.to == db_notification.to
    assert persisted_notification.template_id == sample_template.id
    assert persisted_notification.template_version == version_on_notification
    assert persisted_notification.template_version != sample_template.version
    assert persisted_notification.status == "sent"
    assert persisted_notification.reference == "message_id_from_sns"
    assert not persisted_notification.personalisation


@pytest.mark.parametrize("research_mode, key_type", [(True, KEY_TYPE_NORMAL), (False, KEY_TYPE_TEST)])
def test_should_call_send_sms_response_task_if_research_mode(
    notify_db, sample_service, sample_notification, mocker, research_mode, key_type
):
    reference = str(uuid.uuid4())
    mocker.patch("app.aws_sns_client.send_sms")
    mocker.patch("app.delivery.send_to_providers.send_sms_response", return_value=reference)

    if research_mode:
        sample_service.research_mode = True
        notify_db.session.add(sample_service)
        notify_db.session.commit()

    sample_notification.key_type = key_type

    send_to_providers.send_sms_to_provider(sample_notification)
    assert not aws_sns_client.send_sms.called

    app.delivery.send_to_providers.send_sms_response.assert_called_once_with("sns", sample_notification.to)

    persisted_notification = notifications_dao.get_notification_by_id(sample_notification.id)
    assert persisted_notification.to == sample_notification.to
    assert persisted_notification.template_id == sample_notification.template_id
    assert persisted_notification.status == "sent"
    assert persisted_notification.sent_at <= datetime.utcnow()
    assert persisted_notification.sent_by == "sns"
    assert persisted_notification.reference == reference
    assert not persisted_notification.personalisation


def test_should_not_have_sent_status_if_fake_callback_function_fails(sample_notification, mocker):
    mocker.patch("app.delivery.send_to_providers.send_sms_response", side_effect=HTTPError)

    sample_notification.key_type = KEY_TYPE_TEST

    with pytest.raises(HTTPError):
        send_to_providers.send_sms_to_provider(sample_notification)
    assert sample_notification.status == "created"
    assert sample_notification.sent_by is None


def test_should_not_send_to_provider_when_status_is_not_created(sample_template, mocker):
    notification = save_notification(create_notification(template=sample_template, status="sending"))
    mocker.patch("app.aws_sns_client.send_sms")
    response_mock = mocker.patch("app.delivery.send_to_providers.send_sms_response")

    send_to_providers.send_sms_to_provider(notification)

    app.aws_sns_client.send_sms.assert_not_called()
    response_mock.assert_not_called()


def test_should_send_sms_with_downgraded_content(notify_db_session, mocker):
    # é, o, and u are in GSM.
    # á, ï, grapes, tabs, zero width space and ellipsis are not
    msg = "á é ï o u 🍇 foo\tbar\u200bbaz((misc))…"
    placeholder = "∆∆∆abc"
    gsm_message = "?odz Housing Service: a é i o u ? foo barbaz???abc..."
    service = create_service(service_name="Łódź Housing Service")
    template = create_template(service, content=msg)
    db_notification = save_notification(create_notification(template=template, personalisation={"misc": placeholder}))

    mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")

    send_to_providers.send_sms_to_provider(db_notification)

    aws_sns_client.send_sms.assert_called_once_with(to=ANY, content=gsm_message, reference=ANY, sender=ANY)


def test_send_sms_should_use_service_sms_sender(sample_service, sample_template, mocker):
    mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")

    sms_sender = create_service_sms_sender(service=sample_service, sms_sender="123456", is_default=False)
    db_notification = save_notification(create_notification(template=sample_template, reply_to_text=sms_sender.sms_sender))

    send_to_providers.send_sms_to_provider(
        db_notification,
    )

    app.aws_sns_client.send_sms.assert_called_once_with(to=ANY, content=ANY, reference=ANY, sender=sms_sender.sms_sender)


@pytest.mark.parametrize("research_mode,key_type", [(True, KEY_TYPE_NORMAL), (False, KEY_TYPE_TEST)])
def test_send_email_to_provider_should_call_research_mode_task_response_task_if_research_mode(
    sample_service, sample_email_template, mocker, research_mode, key_type
):
    notification = save_notification(
        create_notification(
            template=sample_email_template,
            to_field="john@smith.com",
            key_type=key_type,
            billable_units=0,
        )
    )
    sample_service.research_mode = research_mode

    reference = str(uuid.uuid4())
    mocker.patch("app.aws_ses_client.send_email")
    mocker.patch("app.delivery.send_to_providers.send_email_response", return_value=reference)

    send_to_providers.send_email_to_provider(notification)

    assert not app.aws_ses_client.send_email.called
    app.delivery.send_to_providers.send_email_response.assert_called_once_with("john@smith.com")
    persisted_notification = Notification.query.filter_by(id=notification.id).one()
    assert persisted_notification.to == "john@smith.com"
    assert persisted_notification.template_id == sample_email_template.id
    assert persisted_notification.status == "sending"
    assert persisted_notification.sent_at <= datetime.utcnow()
    assert persisted_notification.created_at <= datetime.utcnow()
    assert persisted_notification.sent_by == "ses"
    assert persisted_notification.reference == str(reference)
    assert persisted_notification.billable_units == 0


def test_send_email_to_provider_should_not_send_to_provider_when_status_is_not_created(sample_email_template, mocker):
    notification = save_notification(create_notification(template=sample_email_template, status="sending"))
    mocker.patch("app.aws_ses_client.send_email")
    mocker.patch("app.delivery.send_to_providers.send_email_response")

    send_to_providers.send_sms_to_provider(notification)
    app.aws_ses_client.send_email.assert_not_called()
    app.delivery.send_to_providers.send_email_response.assert_not_called()


def test_send_email_should_use_service_reply_to_email(sample_service, sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    db_notification = save_notification(create_notification(template=sample_email_template, reply_to_text="foo@bar.com"))
    create_reply_to_email(service=sample_service, email_address="foo@bar.com")

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    app.aws_ses_client.send_email.assert_called_once_with(
        ANY,
        ANY,
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address="foo@bar.com",
        attachments=[],
    )


def test_send_email_should_use_default_service_reply_to_email_when_two_are_set(sample_service, sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    create_reply_to_email(service=sample_service, email_address="foo@bar.com")
    create_reply_to_email(service=sample_service, email_address="foo_two@bar.com", is_default=False)

    db_notification = save_notification(create_notification(template=sample_email_template, reply_to_text="foo@bar.com"))

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    app.aws_ses_client.send_email.assert_called_once_with(
        ANY,
        ANY,
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address="foo@bar.com",
        attachments=[],
    )


def test_send_email_should_use_non_default_service_reply_to_email_when_it_is_set(sample_service, sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    create_reply_to_email(service=sample_service, email_address="foo@bar.com")
    create_reply_to_email(service=sample_service, email_address="foo_two@bar.com", is_default=False)

    db_notification = save_notification(create_notification(template=sample_email_template, reply_to_text="foo_two@bar.com"))

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    app.aws_ses_client.send_email.assert_called_once_with(
        ANY,
        ANY,
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address="foo_two@bar.com",
        attachments=[],
    )


def test_get_html_email_renderer_should_return_for_normal_service(sample_service):
    options = send_to_providers.get_html_email_options(sample_service)
    assert options["fip_banner_english"] is True
    assert options["fip_banner_french"] is False
    assert "brand_colour" not in options.keys()
    assert "brand_logo" not in options.keys()
    assert "brand_text" not in options.keys()
    assert "brand_name" not in options.keys()


@pytest.mark.parametrize(
    "branding_type, fip_banner_english, fip_banner_french",
    [
        (BRANDING_ORG_NEW, False, False),
        (BRANDING_BOTH_EN, True, False),
        (BRANDING_BOTH_FR, False, True),
        (BRANDING_ORG_BANNER_NEW, False, False),
    ],
)
def test_get_html_email_renderer_with_branding_details(
    branding_type, fip_banner_english, fip_banner_french, notify_db, sample_service
):

    email_branding = EmailBranding(
        brand_type=branding_type,
        colour="#000000",
        logo="justice-league.png",
        name="Justice League",
        text="League of Justice",
    )
    sample_service.email_branding = email_branding
    notify_db.session.add_all([sample_service, email_branding])
    notify_db.session.commit()

    options = send_to_providers.get_html_email_options(sample_service)

    assert options["fip_banner_english"] == fip_banner_english
    assert options["fip_banner_french"] == fip_banner_french
    assert options["brand_colour"] == "#000000"
    assert options["brand_text"] == "League of Justice"
    assert options["brand_name"] == "Justice League"

    if branding_type == BRANDING_ORG_BANNER_NEW:
        assert options["logo_with_background_colour"] is True
    else:
        assert options["logo_with_background_colour"] is False


def test_get_html_email_renderer_with_branding_details_and_render_fip_banner_english_only(notify_db, sample_service):
    sample_service.email_branding = None
    notify_db.session.add_all([sample_service])
    notify_db.session.commit()

    options = send_to_providers.get_html_email_options(sample_service)

    assert options == {
        "fip_banner_english": True,
        "fip_banner_french": False,
        "logo_with_background_colour": False,
    }


def test_get_html_email_renderer_prepends_logo_path(notify_api):
    Service = namedtuple("Service", ["email_branding"])
    EmailBranding = namedtuple("EmailBranding", ["brand_type", "colour", "name", "logo", "text"])

    email_branding = EmailBranding(
        brand_type=BRANDING_ORG_NEW,
        colour="#000000",
        logo="justice-league.png",
        name="Justice League",
        text="League of Justice",
    )
    service = Service(
        email_branding=email_branding,
    )

    renderer = send_to_providers.get_html_email_options(service)
    domain = "https://assets.notification.canada.ca"
    assert renderer["brand_logo"] == "{}{}".format(domain, "/justice-league.png")


def test_get_html_email_renderer_handles_email_branding_without_logo(notify_api):
    Service = namedtuple("Service", ["email_branding"])
    EmailBranding = namedtuple("EmailBranding", ["brand_type", "colour", "name", "logo", "text"])

    email_branding = EmailBranding(
        brand_type=BRANDING_ORG_BANNER_NEW,
        colour="#000000",
        logo=None,
        name="Justice League",
        text="League of Justice",
    )
    service = Service(
        email_branding=email_branding,
    )

    renderer = send_to_providers.get_html_email_options(service)

    assert renderer["fip_banner_english"] is False
    assert renderer["logo_with_background_colour"] is True
    assert renderer["brand_logo"] is None
    assert renderer["brand_text"] == "League of Justice"
    assert renderer["brand_colour"] == "#000000"
    assert renderer["brand_name"] == "Justice League"


def test_should_not_update_notification_if_research_mode_on_exception(sample_service, sample_notification, mocker):
    mock_send_sms = mocker.patch("app.delivery.send_to_providers.send_sms_response", side_effect=Exception())
    sample_service.research_mode = True
    sample_notification.billable_units = 0

    with pytest.raises(Exception):
        send_to_providers.send_sms_to_provider(sample_notification)

    persisted_notification = notifications_dao.get_notification_by_id(sample_notification.id)
    assert persisted_notification.billable_units == 0
    assert mock_send_sms.called


def __update_notification(notification_to_update, research_mode, expected_status):
    if research_mode or notification_to_update.key_type == KEY_TYPE_TEST:
        notification_to_update.status = expected_status


@pytest.mark.parametrize(
    "research_mode,key_type, billable_units, expected_status",
    [
        (True, KEY_TYPE_NORMAL, 0, "delivered"),
        (False, KEY_TYPE_NORMAL, 1, "sent"),
        (False, KEY_TYPE_TEST, 0, "sent"),
        (True, KEY_TYPE_TEST, 0, "sent"),
        (True, KEY_TYPE_TEAM, 0, "delivered"),
        (False, KEY_TYPE_TEAM, 1, "sent"),
    ],
)
def test_should_update_billable_units_and_status_according_to_research_mode_and_key_type(
    sample_template, mocker, research_mode, key_type, billable_units, expected_status
):
    notification = save_notification(
        create_notification(template=sample_template, billable_units=0, status="created", key_type=key_type)
    )
    mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")
    mocker.patch(
        "app.delivery.send_to_providers.send_sms_response",
        side_effect=__update_notification(notification, research_mode, expected_status),
    )

    if research_mode:
        sample_template.service.research_mode = True

    send_to_providers.send_sms_to_provider(notification)
    assert notification.billable_units == billable_units
    assert notification.status == expected_status


def test_should_set_notification_billable_units_if_sending_to_provider_fails(
    sample_notification,
    mocker,
):
    mocker.patch("app.aws_sns_client.send_sms", side_effect=Exception())
    mock_toggle_provider = mocker.patch("app.delivery.send_to_providers.dao_toggle_sms_provider")

    sample_notification.billable_units = 0
    assert sample_notification.sent_by is None

    with pytest.raises(Exception):
        send_to_providers.send_sms_to_provider(sample_notification)

    assert sample_notification.billable_units == 1
    assert mock_toggle_provider.called


@pytest.mark.skip(reason="Currently not supporting international providers")
def test_should_send_sms_to_international_providers(restore_provider_details, sample_sms_template_with_html, sample_user, mocker):
    mocker.patch("app.provider_details.switch_providers.get_user_by_id", return_value=sample_user)

    dao_switch_sms_provider_to_provider_with_identifier("firetext")

    db_notification_uk = save_notification(
        create_notification(
            template=sample_sms_template_with_html,
            to_field="+16135555555",
            personalisation={"name": "Jo"},
            status="created",
            international=False,
            reply_to_text=sample_sms_template_with_html.service.get_default_sms_sender(),
        )
    )

    db_notification_international = save_notification(
        create_notification(
            template=sample_sms_template_with_html,
            to_field="+1613555555",
            personalisation={"name": "Jo"},
            status="created",
            international=False,
            reply_to_text=sample_sms_template_with_html.service.get_default_sms_sender(),
        )
    )

    mocker.patch("app.aws_sns_client.send_sms")

    send_to_providers.send_sms_to_provider(db_notification_uk)

    send_to_providers.send_sms_to_provider(db_notification_international)

    aws_sns_client.send_sms.assert_called_once_with(
        to="601117224412",
        content=ANY,
        reference=str(db_notification_international.id),
        sender=current_app.config["FROM_NUMBER"],
    )

    notification_uk = Notification.query.filter_by(id=db_notification_uk.id).one()
    notification_int = Notification.query.filter_by(id=db_notification_international.id).one()

    assert notification_uk.status == "sending"
    assert notification_uk.sent_by == "firetext"
    assert notification_int.status == "sent"


@pytest.mark.parametrize(
    "sms_sender, expected_sender, prefix_sms, expected_content",
    [
        ("foo", "foo", False, "bar"),
        ("foo", "foo", True, "Sample service: bar"),
        # if 40604 is actually in DB then treat that as if entered manually
        ("40604", "40604", False, "bar"),
        # 'testing' is the FROM_NUMBER during unit tests
        ("testing", "testing", True, "Sample service: bar"),
        ("testing", "testing", False, "bar"),
    ],
)
def test_should_handle_sms_sender_and_prefix_message(
    mocker, sms_sender, prefix_sms, expected_sender, expected_content, notify_db_session
):
    mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")
    service = create_service_with_defined_sms_sender(sms_sender_value=sms_sender, prefix_sms=prefix_sms)
    template = create_template(service, content="bar")
    notification = save_notification(create_notification(template, reply_to_text=sms_sender))

    send_to_providers.send_sms_to_provider(notification)

    aws_sns_client.send_sms.assert_called_once_with(
        content=expected_content,
        sender=expected_sender,
        to=ANY,
        reference=ANY,
    )


def test_send_email_to_provider_uses_reply_to_from_notification(sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    db_notification = save_notification(create_notification(template=sample_email_template, reply_to_text="test@test.com"))

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    app.aws_ses_client.send_email.assert_called_once_with(
        ANY,
        ANY,
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address="test@test.com",
        attachments=[],
    )


def test_send_email_to_provider_should_format_reply_to_email_address(sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    db_notification = save_notification(create_notification(template=sample_email_template, reply_to_text="test@test.com\t"))

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    app.aws_ses_client.send_email.assert_called_once_with(
        ANY,
        ANY,
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address="test@test.com",
        attachments=[],
    )


def test_send_sms_to_provider_should_format_phone_number(sample_notification, mocker):
    sample_notification.to = "+1 650 253 2222"
    send_mock = mocker.patch("app.aws_sns_client.send_sms", return_value="message_id_from_sns")

    send_to_providers.send_sms_to_provider(sample_notification)

    assert send_mock.call_args[1]["to"] == "+16502532222"


def test_send_email_to_provider_should_format_email_address(sample_email_notification, mocker):
    sample_email_notification.to = "test@example.com\t"
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    send_to_providers.send_email_to_provider(sample_email_notification)

    # to_addresses
    send_mock.assert_called_once_with(
        ANY,
        # to_addresses
        "test@example.com",
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address=ANY,
        attachments=[],
    )


def test_notification_can_have_document_attachment_without_mlwr_sid(sample_email_template, mocker):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    mlwr_mock = mocker.patch("app.delivery.send_to_providers.check_mlwr")
    response = document_download_response()
    del response["document"]["mlwr_sid"]
    personalisation = {"file": response}

    db_notification = save_notification(create_notification(template=sample_email_template, personalisation=personalisation))

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    send_mock.assert_called()
    mlwr_mock.assert_not_called()


def test_notification_can_have_document_attachment_if_mlwr_sid_is_false(sample_email_template, mocker):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    mlwr_mock = mocker.patch("app.delivery.send_to_providers.check_mlwr")
    personalisation = {"file": document_download_response({"mlwr_sid": "false"})}

    db_notification = save_notification(create_notification(template=sample_email_template, personalisation=personalisation))

    send_to_providers.send_email_to_provider(
        db_notification,
    )

    send_mock.assert_called()
    mlwr_mock.assert_not_called()


def test_notification_raises_a_retry_exception_if_mlwr_state_is_missing(sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    mocker.patch("app.delivery.send_to_providers.check_mlwr", return_value={})
    personalisation = {"file": document_download_response()}

    db_notification = save_notification(create_notification(template=sample_email_template, personalisation=personalisation))

    with pytest.raises(MalwarePendingException):
        send_to_providers.send_email_to_provider(
            db_notification,
        )


def test_notification_raises_a_retry_exception_if_mlwr_state_is_not_complete(sample_email_template, mocker):
    mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    mocker.patch("app.delivery.send_to_providers.check_mlwr", return_value={"state": "foo"})
    personalisation = {"file": document_download_response()}

    db_notification = save_notification(create_notification(template=sample_email_template, personalisation=personalisation))

    with pytest.raises(MalwarePendingException):
        send_to_providers.send_email_to_provider(
            db_notification,
        )


def test_notification_raises_sets_notification_to_virus_found_if_mlwr_score_is_500(sample_email_template, mocker):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    mocker.patch(
        "app.delivery.send_to_providers.check_mlwr",
        return_value={"state": "completed", "submission": {"max_score": 500}},
    )
    personalisation = {"file": document_download_response()}

    db_notification = save_notification(create_notification(template=sample_email_template, personalisation=personalisation))

    with pytest.raises(NotificationTechnicalFailureException) as e:
        send_to_providers.send_email_to_provider(db_notification)
        assert db_notification.id in e.value
    send_mock.assert_not_called()

    assert Notification.query.get(db_notification.id).status == "virus-scan-failed"


def test_notification_raises_sets_notification_to_virus_found_if_mlwr_score_above_500(sample_email_template, mocker):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    mocker.patch(
        "app.delivery.send_to_providers.check_mlwr",
        return_value={"state": "completed", "submission": {"max_score": 501}},
    )
    personalisation = {"file": document_download_response()}

    db_notification = save_notification(create_notification(template=sample_email_template, personalisation=personalisation))

    with pytest.raises(NotificationTechnicalFailureException) as e:
        send_to_providers.send_email_to_provider(db_notification)
        assert db_notification.id in e.value
    send_mock.assert_not_called()

    assert Notification.query.get(db_notification.id).status == "virus-scan-failed"


@pytest.mark.parametrize(
    "filename_attribute_present, filename, expected_filename",
    [
        (False, "whatever", None),
        (True, None, None),
        (True, "custom_filename.pdf", "custom_filename.pdf"),
    ],
)
def test_notification_document_with_pdf_attachment(
    mocker,
    notify_db,
    notify_db_session,
    filename_attribute_present,
    filename,
    expected_filename,
):
    template = create_sample_email_template(notify_db, notify_db_session, content="Here is your ((file))")
    personalisation = {
        "file": document_download_response(
            {
                "direct_file_url": "http://foo.bar/direct_file_url",
                "url": "http://foo.bar/url",
                "mime_type": "application/pdf",
                "mlwr_sid": "false",
            }
        )
    }
    if filename_attribute_present:
        personalisation["file"]["document"]["filename"] = filename
        personalisation["file"]["document"]["sending_method"] = "attach"
    else:
        personalisation["file"]["document"]["sending_method"] = "link"

    db_notification = save_notification(create_notification(template=template, personalisation=personalisation))

    statsd_mock = mocker.patch("app.delivery.send_to_providers.statsd_client")
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")
    request_mock = mocker.patch(
        "app.delivery.send_to_providers.urllib.request.Request",
        return_value="request_mock",
    )
    # See https://stackoverflow.com/a/34929900
    cm = MagicMock()
    cm.read.return_value = "request_content"
    cm.__enter__.return_value = cm
    urlopen_mock = mocker.patch("app.delivery.send_to_providers.urllib.request.urlopen")
    urlopen_mock.return_value = cm

    send_to_providers.send_email_to_provider(db_notification)

    attachments = []
    if filename_attribute_present:
        request_mock.assert_called_once_with("http://foo.bar/direct_file_url")
        urlopen_mock.assert_called_once_with("request_mock")
        attachments = [
            {
                "data": "request_content",
                "name": expected_filename,
                "mime_type": "application/pdf",
            }
        ]
    send_mock.assert_called_once_with(
        ANY,
        ANY,
        ANY,
        body=ANY,
        html_body=ANY,
        reply_to_address=ANY,
        attachments=attachments,
    )
    if not filename_attribute_present:
        assert "http://foo.bar/url" in send_mock.call_args[1]["html_body"]

    notification = Notification.query.get(db_notification.id)
    assert notification.status == "sending"

    if attachments:
        statsd_calls = statsd_mock.timing_with_dates.call_args_list
        statsd_key = "email.with-attachments.process_type-normal"
        assert call(statsd_key, notification.sent_at, notification.created_at) in statsd_calls
        assert call(statsd_key) in statsd_mock.incr.call_args_list


@pytest.mark.parametrize(
    "sending_method",
    [
        ("attach"),
        ("link"),
    ],
)
def test_notification_with_bad_file_attachment_url(mocker: MockerFixture, notify_db, notify_db_session, sending_method):
    template = create_sample_email_template(notify_db, notify_db_session, content="Here is your ((file))")
    personalisation = {
        "file": document_download_response(
            {
                "direct_file_url": "file://foo.bar/file.txt" if sending_method == "attach" else "http://foo.bar/file.txt",
                "url": "file://foo.bar/file.txt" if sending_method == "link" else "http://foo.bar/file.txt",
                "mime_type": "application/pdf",
                "mlwr_sid": "false",
            }
        )
    }
    personalisation["file"]["document"]["sending_method"] = sending_method
    if sending_method == "attach":
        personalisation["file"]["document"]["filename"] = "file.txt"

    db_notification = save_notification(create_notification(template=template, personalisation=personalisation))

    # See https://stackoverflow.com/a/34929900
    cm = MagicMock()
    cm.read.return_value = "request_content"
    cm.__enter__.return_value = cm
    urlopen_mock = mocker.patch("app.delivery.send_to_providers.urllib.request.urlopen")
    urlopen_mock.return_value = cm

    with pytest.raises(InvalidUrlException):
        send_to_providers.send_email_to_provider(db_notification)


def test_notification_raises_error_if_message_contains_sin_pii_that_passes_luhn(
    sample_email_template_with_html, mocker, notify_api
):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    db_notification = save_notification(
        create_notification(
            template=sample_email_template_with_html,
            to_field="jo.smith@example.com",
            personalisation={"name": "046-454-286"},
        )
    )

    with set_config_values(
        notify_api,
        {
            "SCAN_FOR_PII": "True",
        },
    ):
        with pytest.raises(NotificationTechnicalFailureException) as e:
            send_to_providers.send_email_to_provider(db_notification)
            assert db_notification.id in e.value

    send_mock.assert_not_called()

    assert Notification.query.get(db_notification.id).status == "pii-check-failed"


def test_notification_passes_if_message_contains_sin_pii_that_fails_luhn(sample_email_template_with_html, mocker, notify_api):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    db_notification = save_notification(
        create_notification(
            template=sample_email_template_with_html,
            to_field="jo.smith@example.com",
            personalisation={"name": "123-456-789"},
        )
    )

    send_to_providers.send_email_to_provider(db_notification)

    send_mock.assert_called()

    assert Notification.query.get(db_notification.id).status == "sending"


def test_notification_passes_if_message_contains_phone_number(sample_email_template_with_html, mocker):
    send_mock = mocker.patch("app.aws_ses_client.send_email", return_value="reference")

    db_notification = save_notification(
        create_notification(
            template=sample_email_template_with_html,
            to_field="jo.smith@example.com",
            personalisation={"name": "123-456-7890"},
        )
    )

    send_to_providers.send_email_to_provider(db_notification)

    send_mock.assert_called()

    assert Notification.query.get(db_notification.id).status == "sending"


def test_is_service_allowed_html(sample_service: Service, notify_api):
    assert not send_to_providers.is_service_allowed_html(sample_service)
    with set_config_values(
        notify_api,
        {
            "ALLOW_HTML_SERVICE_IDS": str(sample_service.id),
        },
    ):
        assert send_to_providers.is_service_allowed_html(sample_service)
