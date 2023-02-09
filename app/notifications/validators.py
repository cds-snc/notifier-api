import base64
import functools
from datetime import datetime, time, timedelta

from flask import current_app
from notifications_utils import SMS_CHAR_COUNT_LIMIT
from notifications_utils.clients.redis import (
    daily_limit_cache_key,
    near_daily_limit_cache_key,
    near_sms_daily_limit_cache_key,
    over_daily_limit_cache_key,
    over_sms_daily_limit_cache_key,
    rate_limit_cache_key,
)
from notifications_utils.recipients import (
    get_international_phone_info,
    validate_and_format_email_address,
    validate_and_format_phone_number,
)
from notifications_utils.statsd_decorators import statsd_catch
from sqlalchemy.orm.exc import NoResultFound

from app import redis_store
from app.dao import services_dao, templates_dao
from app.dao.service_email_reply_to_dao import dao_get_reply_to_by_id
from app.dao.service_letter_contact_dao import dao_get_letter_contact_by_id
from app.dao.service_sms_sender_dao import dao_get_service_sms_senders_by_id
from app.models import (
    EMAIL_TYPE,
    INTERNATIONAL_SMS_TYPE,
    KEY_TYPE_TEAM,
    KEY_TYPE_TEST,
    LETTER_TYPE,
    SCHEDULE_NOTIFICATIONS,
    SMS_TYPE,
    ApiKey,
    ApiKeyType,
    NotificationType,
    Permission,
    Service,
    Template,
    TemplateType,
)
from app.notifications.process_notifications import create_content_for_notification
from app.service.sender import send_notification_to_service_users
from app.service.utils import service_allowed_to_send_to
from app.sms_fragment_utils import (
    fetch_todays_requested_sms_count,
    increment_todays_requested_sms_count,
)
from app.utils import get_document_url, get_public_notify_type_text, is_blank
from app.v2.errors import (
    BadRequestError,
    LiveServiceTooManyRequestsError,
    LiveServiceTooManySMSRequestsError,
    RateLimitError,
    TrialServiceTooManyRequestsError,
    TrialServiceTooManySMSRequestsError,
)

NEAR_DAILY_LIMIT_PERCENTAGE = 80 / 100


def check_service_over_api_rate_limit(service: Service, api_key: ApiKey):
    if current_app.config["API_RATE_LIMIT_ENABLED"] and current_app.config["REDIS_ENABLED"]:
        cache_key = rate_limit_cache_key(service.id, api_key.key_type)
        rate_limit = service.rate_limit
        interval = 60
        if redis_store.exceeded_rate_limit(cache_key, rate_limit, interval):
            current_app.logger.info("service {} has been rate limited for throughput".format(service.id))
            raise RateLimitError(rate_limit, interval, api_key.key_type)


@statsd_catch(
    namespace="validators",
    counter_name="rate_limit.trial_service_daily",
    exception=TrialServiceTooManyRequestsError,
)
@statsd_catch(
    namespace="validators",
    counter_name="rate_limit.live_service_daily",
    exception=LiveServiceTooManyRequestsError,
)
def check_service_over_daily_message_limit(key_type: ApiKeyType, service: Service):
    if key_type != KEY_TYPE_TEST and current_app.config["REDIS_ENABLED"]:
        cache_key = daily_limit_cache_key(service.id)
        messages_sent = redis_store.get(cache_key)
        if not messages_sent:
            messages_sent = services_dao.fetch_todays_total_message_count(service.id)
            redis_store.set(cache_key, messages_sent, ex=int(timedelta(hours=2).total_seconds()))

        warn_about_daily_message_limit(service, int(messages_sent))


@statsd_catch(
    namespace="validators",
    counter_name="rate_limit.trial_service_daily_sms",
    exception=TrialServiceTooManySMSRequestsError,
)
@statsd_catch(
    namespace="validators",
    counter_name="rate_limit.live_service_daily_sms",
    exception=LiveServiceTooManySMSRequestsError,
)
def check_sms_daily_limit(service: Service, requested_sms=0):
    messages_sent = fetch_todays_requested_sms_count(service.id)
    over_sms_daily_limit = (messages_sent + requested_sms) > service.sms_daily_limit

    # Send a warning when reaching the daily message limit
    if not over_sms_daily_limit:
        return

    current_app.logger.info(
        f"service {service.id} is exceeding their daily sms limit [total sent today: {int(messages_sent)} limit: {service.sms_daily_limit}, attempted send: {requested_sms}"
    )
    if service.restricted:
        raise TrialServiceTooManySMSRequestsError(service.sms_daily_limit)
    else:
        raise LiveServiceTooManySMSRequestsError(service.sms_daily_limit)


def send_warning_sms_limit_emails_if_needed(service: Service):
    todays_requested_sms = fetch_todays_requested_sms_count(service.id)
    nearing_sms_daily_limit = todays_requested_sms >= NEAR_DAILY_LIMIT_PERCENTAGE * service.sms_daily_limit
    at_or_over_sms_daily_limit = todays_requested_sms >= service.sms_daily_limit
    current_time = datetime.utcnow().isoformat()
    cache_expiration = int(time_until_end_of_day().total_seconds())

    # Send a warning when reaching 80% of the daily limit
    if nearing_sms_daily_limit:
        cache_key = near_sms_daily_limit_cache_key(service.id)
        if not redis_store.get(cache_key):
            send_near_sms_limit_email(service)
            redis_store.set(cache_key, current_time, ex=cache_expiration)

    # Send a warning when reaching the daily message limit
    if at_or_over_sms_daily_limit:
        cache_key = over_sms_daily_limit_cache_key(service.id)
        if not redis_store.get(cache_key):
            send_sms_limit_reached_email(service)
            redis_store.set(cache_key, current_time, ex=cache_expiration)


def time_until_end_of_day() -> timedelta:
    """
    Get timedelta until end of day on the datetime passed, or current time.
    """
    dt = datetime.now()
    tomorrow = dt + timedelta(days=1)
    return datetime.combine(tomorrow, time.min) - dt


def check_sms_limit_increment_redis_send_warnings_if_needed(service: Service, requested_sms=0) -> None:
    if not current_app.config["FF_SPIKE_SMS_DAILY_LIMIT"]:
        return
    if not current_app.config["REDIS_ENABLED"]:
        return

    check_sms_daily_limit(service, requested_sms)
    increment_todays_requested_sms_count(service.id, requested_sms)
    send_warning_sms_limit_emails_if_needed(service)


def check_rate_limiting(service: Service, api_key: ApiKey):
    check_service_over_api_rate_limit(service, api_key)
    check_service_over_daily_message_limit(api_key.key_type, service)


def warn_about_daily_message_limit(service: Service, messages_sent):
    nearing_daily_message_limit = messages_sent >= NEAR_DAILY_LIMIT_PERCENTAGE * service.message_limit
    over_daily_message_limit = messages_sent >= service.message_limit

    current_time = datetime.utcnow().isoformat()
    cache_expiration = int(timedelta(days=1).total_seconds())

    # Send a warning when reaching 80% of the daily limit
    if nearing_daily_message_limit:
        cache_key = near_daily_limit_cache_key(service.id)
        if not redis_store.get(cache_key):
            redis_store.set(cache_key, current_time, ex=cache_expiration)
            send_notification_to_service_users(
                service_id=service.id,
                template_id=current_app.config["NEAR_DAILY_LIMIT_TEMPLATE_ID"],
                personalisation={
                    "service_name": service.name,
                    "contact_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
                    "message_limit_en": "{:,}".format(service.message_limit),
                    "message_limit_fr": "{:,}".format(service.message_limit).replace(",", " "),
                },
                include_user_fields=["name"],
            )
            current_app.logger.info(
                f"service {service.id} is approaching its daily limit, sent {int(messages_sent)} limit {service.message_limit}"
            )

    # Send a warning when reaching the daily message limit
    if over_daily_message_limit:
        cache_key = over_daily_limit_cache_key(service.id)
        if not redis_store.get(cache_key):
            redis_store.set(cache_key, current_time, ex=cache_expiration)
            send_notification_to_service_users(
                service_id=service.id,
                template_id=current_app.config["REACHED_DAILY_LIMIT_TEMPLATE_ID"],
                personalisation={
                    "service_name": service.name,
                    "contact_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
                    "message_limit_en": "{:,}".format(service.message_limit),
                    "message_limit_fr": "{:,}".format(service.message_limit).replace(",", " "),
                },
                include_user_fields=["name"],
            )

        current_app.logger.info(
            f"service {service.id} has been rate limited for daily use sent {int(messages_sent)} limit {service.message_limit}"
        )
        if service.restricted:
            raise TrialServiceTooManyRequestsError(service.message_limit)
        else:
            raise LiveServiceTooManyRequestsError(service.message_limit)


def send_near_sms_limit_email(service: Service):
    send_notification_to_service_users(
        service_id=service.id,
        template_id=current_app.config["NEAR_DAILY_SMS_LIMIT_TEMPLATE_ID"]
        if current_app.config["FF_SPIKE_SMS_DAILY_LIMIT"]
        else current_app.config["NEAR_DAILY_LIMIT_TEMPLATE_ID"],
        personalisation={
            "service_name": service.name,
            "contact_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
            "message_limit_en": "{:,}".format(service.sms_daily_limit),
            "message_limit_fr": "{:,}".format(service.sms_daily_limit).replace(",", " "),
        },
        include_user_fields=["name"],
    )
    current_app.logger.info(f"service {service.id} is approaching its daily sms limit of {service.sms_daily_limit}")


def send_sms_limit_reached_email(service: Service):
    send_notification_to_service_users(
        service_id=service.id,
        template_id=current_app.config["REACHED_DAILY_SMS_LIMIT_TEMPLATE_ID"]
        if current_app.config["FF_SPIKE_SMS_DAILY_LIMIT"]
        else current_app.config["REACHED_DAILY_LIMIT_TEMPLATE_ID"],
        personalisation={
            "service_name": service.name,
            "contact_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
            "message_limit_en": "{:,}".format(service.sms_daily_limit),
            "message_limit_fr": "{:,}".format(service.sms_daily_limit).replace(",", " "),
        },
        include_user_fields=["name"],
    )


def check_template_is_for_notification_type(notification_type: NotificationType, template_type: TemplateType):
    if notification_type != template_type:
        message = "{0} template is not suitable for {1} notification".format(template_type, notification_type)
        raise BadRequestError(fields=[{"template": message}], message=message)


def check_template_is_active(template):
    if template.archived:
        raise BadRequestError(
            fields=[{"template": f"Template {template.id} has been deleted"}],
            message=f"Template {template.id} has been deleted",
        )


def service_can_send_to_recipient(send_to, key_type: ApiKeyType, service: Service, allow_safelisted_recipients=True):
    if not service_allowed_to_send_to(send_to, service, key_type, allow_safelisted_recipients):
        # FIXME: hard code it for now until we can get en/fr specific links and text
        if key_type == KEY_TYPE_TEAM:
            message = (
                f"Can’t send to this recipient using a team-only API key (service {service.id}) "
                f'- see {get_document_url("en", "keys.html#team-and-safelist")}'
            )
        else:
            message = (
                "Can’t send to this recipient when service is in trial mode " f'– see {get_document_url("en", "keys.html#live")}'
            )
        raise BadRequestError(message=message, status_code=400)


def service_has_permission(notify_type, permissions: list[Permission]):
    return notify_type in [p.permission for p in permissions]


def check_service_has_permission(notify_type, permissions: list[Permission]):
    if not service_has_permission(notify_type, permissions):
        raise BadRequestError(
            message="Service is not allowed to send {}".format(get_public_notify_type_text(notify_type, plural=True))
        )


def check_service_can_schedule_notification(permissions: list[Permission], scheduled_for):
    if scheduled_for:
        if not service_has_permission(SCHEDULE_NOTIFICATIONS, permissions):
            raise BadRequestError(message="Cannot schedule notifications (this feature is invite-only)")


def validate_and_format_recipient(
    send_to, key_type: ApiKeyType, service: Service, notification_type: NotificationType, allow_safelisted_recipients=True
):
    if send_to is None:
        raise BadRequestError(message="Recipient can't be empty")

    service_can_send_to_recipient(send_to, key_type, service, allow_safelisted_recipients)

    if notification_type == SMS_TYPE:
        international_phone_info = get_international_phone_info(send_to)

        if international_phone_info.international and INTERNATIONAL_SMS_TYPE not in [p.permission for p in service.permissions]:
            raise BadRequestError(message="Cannot send to international mobile numbers")

        return validate_and_format_phone_number(number=send_to, international=international_phone_info.international)
    elif notification_type == EMAIL_TYPE:
        return validate_and_format_email_address(email_address=send_to)


def check_sms_content_char_count(content_count, service_name, prefix_sms: bool):
    content_length = (
        content_count + len(service_name) + 2 if prefix_sms else content_count
    )  # the +2 is to account for the ': ' that is added to the service name

    if content_length > SMS_CHAR_COUNT_LIMIT:
        message = "Content for template has a character count greater than the limit of {}".format(SMS_CHAR_COUNT_LIMIT)
        raise BadRequestError(message=message)


def check_content_is_not_blank(content):
    if is_blank(content):
        message = "Message is empty or just whitespace"
        raise BadRequestError(message=message)


def validate_template_exists(template_id, service: Service):
    template = check_template_exists_by_id_and_service(template_id, service)
    check_template_is_active(template)

    return template


def validate_template(template_id, personalisation, service: Service, notification_type: NotificationType):
    template = check_template_exists_by_id_and_service(template_id, service)
    check_template_is_for_notification_type(notification_type, template.template_type)
    check_template_is_active(template)

    template_with_content: Template = create_content_for_notification(template, personalisation)
    if template.template_type == SMS_TYPE:
        check_sms_content_char_count(template_with_content.content_count, service.name, service.prefix_sms)

    check_content_is_not_blank(template_with_content)

    return template, template_with_content


def check_template_exists_by_id_and_service(template_id, service: Service) -> Template:
    try:
        return templates_dao.dao_get_template_by_id_and_service_id(template_id=template_id, service_id=service.id)
    except NoResultFound:
        message = "Template not found"
        raise BadRequestError(message=message, fields=[{"template": message}])


def check_reply_to(service_id, reply_to_id, type_):
    if type_ == EMAIL_TYPE:
        return check_service_email_reply_to_id(service_id, reply_to_id, type_)
    elif type_ == SMS_TYPE:
        return check_service_sms_sender_id(service_id, reply_to_id, type_)
    elif type_ == LETTER_TYPE:
        return check_service_letter_contact_id(service_id, reply_to_id, type_)


def check_service_email_reply_to_id(service_id, reply_to_id, notification_type: NotificationType):
    if reply_to_id:
        try:
            return dao_get_reply_to_by_id(service_id, reply_to_id).email_address
        except NoResultFound:
            message = "email_reply_to_id {} does not exist in database for service id {}".format(reply_to_id, service_id)
            raise BadRequestError(message=message)


def check_service_sms_sender_id(service_id, sms_sender_id, notification_type: NotificationType):
    if sms_sender_id:
        try:
            return dao_get_service_sms_senders_by_id(service_id, sms_sender_id).sms_sender
        except NoResultFound:
            message = "sms_sender_id {} does not exist in database for service id {}".format(sms_sender_id, service_id)
            raise BadRequestError(message=message)


def check_service_letter_contact_id(service_id, letter_contact_id, notification_type: NotificationType):
    if letter_contact_id:
        try:
            return dao_get_letter_contact_by_id(service_id, letter_contact_id).contact_block
        except NoResultFound:
            message = "letter_contact_id {} does not exist in database for service id {}".format(letter_contact_id, service_id)
            raise BadRequestError(message=message)


def validate_personalisation_and_decode_files(json_personalisation):
    errors = []
    json_personalisation, errors_vars = validate_personalisation_size(json_personalisation)
    json_personalisation, errors_num_file = validate_personalisation_num_files(json_personalisation)
    json_personalisation, errors_files = decode_personalisation_files(json_personalisation)
    errors.extend(errors_vars)
    errors.extend(errors_num_file)
    errors.extend(errors_files)
    return json_personalisation, errors


def validate_personalisation_size(json_personalisation):
    errors = []
    values = [v for _, v in json_personalisation.items() if not isinstance(v, dict)]
    concat_values = functools.reduce(lambda v1, v2: f"{v1}{v2}", values, "")
    size_all_values = len(concat_values)
    size_limit = current_app.config["PERSONALISATION_SIZE_LIMIT"]
    current_app.logger.debug(f"Personalization size of variables detected at {size_all_values} bytes.")
    if size_all_values > size_limit:
        errors.append(
            {
                "error": "ValidationError",
                "message": f"Personalisation variables size of {size_all_values} bytes is greater than allowed limit of {size_limit} bytes.",
            }
        )

    return json_personalisation, errors


def validate_personalisation_num_files(json_personalisation):
    errors = []
    file_keys = [k for k, v in json_personalisation.items() if isinstance(v, dict) and "file" in v]
    files_num = len(file_keys)
    num_limit = current_app.config["ATTACHMENT_NUM_LIMIT"]
    if files_num > num_limit:
        current_app.logger.debug(f"Number of file attachments detected at {files_num}.")
        errors.append(
            {
                "error": "ValidationError",
                "message": f"File number exceed allowed limits of {num_limit} with number of {files_num}.",
            }
        )
    return json_personalisation, errors


def decode_personalisation_files(json_personalisation):
    errors = []
    file_keys = [k for k, v in json_personalisation.items() if isinstance(v, dict) and "file" in v]
    for key in file_keys:
        try:
            json_personalisation[key]["file"] = base64.b64decode(json_personalisation[key]["file"])
            personalisation_size = len(json_personalisation[key]["file"])
            current_app.logger.debug(f"File size detected at {personalisation_size} bytes.")
            size_limit = current_app.config["ATTACHMENT_SIZE_LIMIT"]
            if personalisation_size > size_limit:
                filename = json_personalisation[key]["filename"]
                errors.append(
                    {
                        "error": "ValidationError",
                        "message": f"{key} : File size for {filename} is {personalisation_size} and greater than allowed limit of {size_limit}.",
                    }
                )
        except Exception as e:
            errors.append(
                {
                    "error": "ValidationError",
                    "message": f"{key} : {str(e)} : Error decoding base64 field",
                }
            )
    return json_personalisation, errors
