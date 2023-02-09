import os
import re
import urllib.request
from datetime import datetime
from typing import Dict
from uuid import UUID

from flask import current_app
from notifications_utils.recipients import (
    validate_and_format_email_address,
    validate_and_format_phone_number,
)
from notifications_utils.template import (
    HTMLEmailTemplate,
    PlainTextEmailTemplate,
    SMSMessageTemplate,
)

from app import clients, statsd_client
from app.celery.research_mode_tasks import send_email_response, send_sms_response
from app.config import Config
from app.dao.notifications_dao import dao_update_notification
from app.dao.provider_details_dao import (
    dao_toggle_sms_provider,
    get_provider_details_by_notification_type,
)
from app.dao.templates_dao import dao_get_template_by_id
from app.exceptions import InvalidUrlException, NotificationTechnicalFailureException
from app.models import (
    BRANDING_BOTH_EN,
    BRANDING_BOTH_FR,
    BRANDING_ORG_BANNER_NEW,
    EMAIL_TYPE,
    KEY_TYPE_TEST,
    NOTIFICATION_CONTAINS_PII,
    NOTIFICATION_SENDING,
    NOTIFICATION_SENT,
    NOTIFICATION_TECHNICAL_FAILURE,
    SMS_TYPE,
    Notification,
    Service,
)
from app.utils import get_logo_url, is_blank


def send_sms_to_provider(notification):
    service = notification.service

    if not service.active:
        inactive_service_failure(notification=notification)
        return

    if notification.status == "created":
        provider = provider_to_use(
            SMS_TYPE,
            notification.id,
            notification.international,
            notification.reply_to_text,
        )

        template_dict = dao_get_template_by_id(notification.template_id, notification.template_version).__dict__

        template = SMSMessageTemplate(
            template_dict,
            values=notification.personalisation,
            prefix=service.name,
            show_prefix=service.prefix_sms,
        )

        if is_blank(template):
            empty_message_failure(notification=notification)
            return

        if service.research_mode or notification.key_type == KEY_TYPE_TEST:
            notification.reference = send_sms_response(provider.get_name(), notification.to)
            update_notification_to_sending(notification, provider)

        elif (
            validate_and_format_phone_number(notification.to, international=notification.international)
            == Config.INTERNAL_TEST_NUMBER
        ):
            current_app.logger.info(f"notification {notification.id} sending to internal test number. Not sending to AWS")
            notification.reference = send_sms_response(provider.get_name(), notification.to)
            update_notification_to_sending(notification, provider)

        else:
            try:
                reference = provider.send_sms(
                    to=validate_and_format_phone_number(notification.to, international=notification.international),
                    content=str(template),
                    reference=str(notification.id),
                    sender=notification.reply_to_text,
                )
            except Exception as e:
                notification.billable_units = template.fragment_count
                dao_update_notification(notification)
                dao_toggle_sms_provider(provider.name)
                raise e
            else:
                notification.reference = reference
                notification.billable_units = template.fragment_count
                update_notification_to_sending(notification, provider)

        # Record StatsD stats to compute SLOs
        statsd_client.timing_with_dates("sms.total-time", notification.sent_at, notification.created_at)
        statsd_key = f"sms.process_type-{template_dict['process_type']}"
        statsd_client.timing_with_dates(statsd_key, notification.sent_at, notification.created_at)
        statsd_client.incr(statsd_key)


def is_service_allowed_html(service: Service) -> bool:
    """
    If a service id is present in ALLOW_HTML_SERVICE_IDS, then they are allowed to put html
    in email templates.
    """
    return str(service.id) in current_app.config["ALLOW_HTML_SERVICE_IDS"]


# Prevent URL patterns like file:// ftp:// that may lead to security local file read vulnerabilities
def check_file_url(file_info: Dict[str, str], notification_id: UUID):
    if file_info.get("sending_method") == "attach":
        url_key = "direct_file_url"
    else:
        url_key = "url"

    if not file_info[url_key].lower().startswith("http"):
        current_app.logger.error(f"Notification {notification_id} contains an invalid {url_key} {file_info[url_key]}")
        raise InvalidUrlException


def send_email_to_provider(notification: Notification):
    current_app.logger.info(f"Sending email to provider for notification id {notification.id}")
    service = notification.service
    if not service.active:
        inactive_service_failure(notification=notification)
        return
    if notification.status == "created":
        provider = provider_to_use(EMAIL_TYPE, notification.id)

        # Extract any file objects from the personalization
        file_keys = [k for k, v in (notification.personalisation or {}).items() if isinstance(v, dict) and "document" in v]
        attachments = []

        personalisation_data = notification.personalisation.copy()

        for key in file_keys:
            check_file_url(personalisation_data[key]["document"], notification.id)
            sending_method = personalisation_data[key]["document"].get("sending_method")
<<<<<<< HEAD
=======

>>>>>>> main
            if sending_method == "attach":
                try:

                    req = urllib.request.Request(personalisation_data[key]["document"]["direct_file_url"])
                    with urllib.request.urlopen(req) as response:
                        buffer = response.read()
                        filename = personalisation_data[key]["document"].get("filename")
                        mime_type = personalisation_data[key]["document"].get("mime_type")
                        attachments.append(
                            {
                                "name": filename,
                                "data": buffer,
                                "mime_type": mime_type,
                            }
                        )
                except Exception:
                    current_app.logger.error(
                        "Could not download and attach {}".format(personalisation_data[key]["document"]["direct_file_url"])
                    )
                del personalisation_data[key]
            else:
                personalisation_data[key] = personalisation_data[key]["document"]["url"]

        template_dict = dao_get_template_by_id(notification.template_id, notification.template_version).__dict__

        # Local Jinja support - Add USE_LOCAL_JINJA_TEMPLATES=True to .env
        # Add a folder to the project root called 'jinja_templates'
        # with a copy of 'email_template.jinja2' from notification-utils repo
        debug_template_path = (
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if os.environ.get("USE_LOCAL_JINJA_TEMPLATES") == "True"
            else None
        )
        html_email = HTMLEmailTemplate(
            template_dict,
            values=personalisation_data,
            jinja_path=debug_template_path,
            allow_html=is_service_allowed_html(service),
            **get_html_email_options(service),
        )

        plain_text_email = PlainTextEmailTemplate(template_dict, values=personalisation_data)

        if current_app.config["SCAN_FOR_PII"]:
            contains_pii(notification, str(plain_text_email))

        current_app.logger.info(
            f"Trying to update notification id {notification.id} with service research {service.research_mode} or key type {notification.key_type}"
        )
        if service.research_mode or notification.key_type == KEY_TYPE_TEST:
            notification.reference = send_email_response(notification.to)
            update_notification_to_sending(notification, provider)
        else:
            if service.sending_domain is None or service.sending_domain.strip() == "":
                sending_domain = current_app.config["NOTIFY_EMAIL_DOMAIN"]
            else:
                sending_domain = service.sending_domain

            from_address = '"{}" <{}@{}>'.format(service.name, service.email_from, sending_domain)

            email_reply_to = notification.reply_to_text

            reference = provider.send_email(
                from_address,
                validate_and_format_email_address(notification.to),
                plain_text_email.subject,
                body=str(plain_text_email),
                html_body=str(html_email),
                reply_to_address=validate_and_format_email_address(email_reply_to) if email_reply_to else None,
                attachments=attachments,
            )
            notification.reference = reference
            update_notification_to_sending(notification, provider)
        current_app.logger.info(f"Notification id {notification.id} status in sending")

        # Record StatsD stats to compute SLOs
        statsd_client.timing_with_dates("email.total-time", notification.sent_at, notification.created_at)
        attachments_category = "with-attachments" if attachments else "no-attachments"
        statsd_key = f"email.{attachments_category}.process_type-{template_dict['process_type']}"
        statsd_client.timing_with_dates(statsd_key, notification.sent_at, notification.created_at)
        statsd_client.incr(statsd_key)


def update_notification_to_sending(notification, provider):
    notification.sent_at = datetime.utcnow()
    notification.sent_by = provider.get_name()
    notification.status = NOTIFICATION_SENT if notification.notification_type == "sms" else NOTIFICATION_SENDING
    dao_update_notification(notification)


def provider_to_use(notification_type, notification_id, international=False, sender=None):
    active_providers_in_order = [
        p for p in get_provider_details_by_notification_type(notification_type, international) if p.active
    ]

    if not active_providers_in_order:
        current_app.logger.error("{} {} failed as no active providers".format(notification_type, notification_id))
        raise Exception("No active {} providers".format(notification_type))

    return clients.get_client_by_name_and_type(active_providers_in_order[0].identifier, notification_type)


def get_html_email_options(service: Service):
    if service.email_branding is None:
        if service.default_branding_is_french is True:
            return {
                "fip_banner_english": False,
                "fip_banner_french": True,
                "logo_with_background_colour": False,
            }
        else:
            return {
                "fip_banner_english": True,
                "fip_banner_french": False,
                "logo_with_background_colour": False,
            }

    logo_url = get_logo_url(service.email_branding.logo) if service.email_branding.logo else None

    return {
        "fip_banner_english": service.email_branding.brand_type == BRANDING_BOTH_EN,
        "fip_banner_french": service.email_branding.brand_type == BRANDING_BOTH_FR,
        "logo_with_background_colour": service.email_branding.brand_type == BRANDING_ORG_BANNER_NEW,
        "brand_colour": service.email_branding.colour,
        "brand_logo": logo_url,
        "brand_text": service.email_branding.text,
        "brand_name": service.email_branding.name,
    }


def inactive_service_failure(notification):
    notification.status = NOTIFICATION_TECHNICAL_FAILURE
    dao_update_notification(notification)
    raise NotificationTechnicalFailureException(
        "Send {} for notification id {} to provider is not allowed: service {} is inactive".format(
            notification.notification_type, notification.id, notification.service_id
        )
    )


def empty_message_failure(notification):
    notification.status = NOTIFICATION_TECHNICAL_FAILURE
    dao_update_notification(notification)
    current_app.logger.error(
        "Send {} for notification id {} (service {}) is not allowed: empty message".format(
            notification.notification_type, notification.id, notification.service_id
        )
    )


def contains_pii(notification, text_content):
    for sin in re.findall(r"\s\d{3}-\d{3}-\d{3}\s", text_content):
        if luhn(sin.replace("-", "").strip()):
            fail_pii(notification, "Social Insurance Number")
            return


def fail_pii(notification, pii_type):
    notification.status = NOTIFICATION_CONTAINS_PII
    dao_update_notification(notification)
    raise NotificationTechnicalFailureException(
        "Send {} for notification id {} to provider is not allowed. Notification contains PII: {}".format(
            notification.notification_type, notification.id, pii_type
        )
    )


def luhn(n):
    r = [int(ch) for ch in n][::-1]
    return (sum(r[0::2]) + sum(sum(divmod(d * 2, 10)) for d in r[1::2])) % 10 == 0
