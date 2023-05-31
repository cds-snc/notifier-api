import base64
import json
import pickle
import uuid
from datetime import datetime, timedelta

import pwnedpasswords
from fido2 import cbor
from fido2.client import ClientData
from fido2.ctap2 import AuthenticatorData
from flask import Blueprint, abort, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from app import salesforce_client
from app.clients.freshdesk import Freshdesk
from app.clients.salesforce.salesforce_engagement import ENGAGEMENT_STAGE_ACTIVATION
from app.config import Config, QueueNames
from app.dao.fido2_key_dao import (
    create_fido2_session,
    decode_and_register,
    delete_fido2_key,
    get_fido2_session,
    list_fido2_keys,
    save_fido2_key,
)
from app.dao.login_event_dao import list_login_events, save_login_event
from app.dao.permissions_dao import permission_dao
from app.dao.service_user_dao import dao_get_service_user, dao_update_service_user
from app.dao.services_dao import dao_fetch_service_by_id, dao_update_service
from app.dao.template_folder_dao import dao_get_template_folder_by_id_and_service_id
from app.dao.templates_dao import dao_get_template_by_id
from app.dao.users_dao import (
    count_user_verify_codes,
    create_secret_code,
    create_user_code,
    dao_archive_user,
    get_user_and_accounts,
    get_user_by_email,
    get_user_by_id,
    get_user_code,
    get_users_by_partial_email,
    increment_failed_login_count,
    reset_failed_login_count,
    save_model_user,
    save_user_attribute,
    update_user_password,
    use_user_code,
    verify_within_time,
)
from app.errors import InvalidRequest, register_errors
from app.models import (
    EMAIL_TYPE,
    KEY_TYPE_NORMAL,
    SMS_TYPE,
    Fido2Key,
    LoginEvent,
    Permission,
    Service,
)
from app.notifications.process_notifications import (
    persist_notification,
    send_notification_to_queue,
)
from app.schema_validation import validate
from app.schemas import (
    create_user_schema,
    email_data_request_schema,
    partial_email_data_request_schema,
    user_update_password_schema_load_json,
    user_update_schema_load_json,
)
from app.user.contact_request import ContactRequest
from app.user.users_schema import (
    fido2_key_schema,
    post_send_user_email_code_schema,
    post_send_user_sms_code_schema,
    post_set_permissions_schema,
    post_verify_code_schema,
)
from app.utils import get_logo_url, update_dct_to_str, url_with_token

user_blueprint = Blueprint("user", __name__)
register_errors(user_blueprint)


@user_blueprint.errorhandler(IntegrityError)
def handle_integrity_error(exc):
    """
    Handle integrity errors caused by the auth type/mobile number check constraint
    """
    if "ck_users_mobile_or_email_auth" in str(exc):
        # we don't expect this to trip, so still log error
        current_app.logger.exception("Check constraint ck_users_mobile_or_email_auth triggered")
        return (
            jsonify(
                result="error",
                message="Mobile number must be set if auth_type is set to sms_auth",
            ),
            400,
        )

    raise exc


@user_blueprint.route("", methods=["POST"])
def create_user():
    # import pdb; pdb.set_trace()
    user_to_create, errors = create_user_schema.load(request.get_json())
    req_json = request.get_json()

    password = req_json.get("password", None)
    if not password:
        errors.update({"password": ["Missing data for required field."]})
        raise InvalidRequest(errors, status_code=400)
    else:
        response = pwnedpasswords.check(password)
        if response > 0:
            errors.update({"password": ["Password is not allowed."]})
            raise InvalidRequest(errors, status_code=400)

    save_model_user(user_to_create, pwd=req_json.get("password"))
    result = user_to_create.serialize()
    return jsonify(data=result), 201


@user_blueprint.route("/<uuid:user_id>", methods=["POST"])
def update_user_attribute(user_id):
    user_to_update = get_user_by_id(user_id=user_id)
    req_json = request.get_json()
    if "updated_by" in req_json:
        updated_by = get_user_by_id(user_id=req_json.pop("updated_by"))
    else:
        updated_by = None

    update_dct, errors = user_update_schema_load_json.load(req_json)
    if errors:
        raise InvalidRequest(errors, status_code=400)

    save_user_attribute(user_to_update, update_dict=update_dct)

    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])

    # Alert user that account change took place
    user_alert_dct = update_dct.copy()
    user_alert_dct.pop("blocked", None)
    user_alert_dct.pop("current_session_id", None)
    if not updated_by and user_alert_dct:
        _update_alert(user_to_update, user_alert_dct)

    # Alert that team member edit user
    if updated_by:
        if "email_address" in update_dct:
            template = dao_get_template_by_id(current_app.config["TEAM_MEMBER_EDIT_EMAIL_TEMPLATE_ID"])
            recipient = user_to_update.email_address
            reply_to = template.service.get_default_reply_to_email_address()
        elif "mobile_number" in update_dct:
            template = dao_get_template_by_id(current_app.config["TEAM_MEMBER_EDIT_MOBILE_TEMPLATE_ID"])
            recipient = user_to_update.mobile_number
            reply_to = template.service.get_default_sms_sender()
        else:
            return jsonify(data=user_to_update.serialize()), 200

        saved_notification = persist_notification(
            template_id=template.id,
            template_version=template.version,
            recipient=recipient,
            service=service,
            personalisation={
                "name": user_to_update.name,
                "servicemanagername": updated_by.name,
                "email address": user_to_update.email_address,
            },
            notification_type=template.template_type,
            api_key_id=None,
            key_type=KEY_TYPE_NORMAL,
            reply_to_text=reply_to,
        )

        send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)

    if current_app.config["FF_SALESFORCE_CONTACT"]:
        try:
            updated_user = get_user_by_id(user_id=user_id)
            salesforce_client.contact_update(updated_user)
        except Exception as e:
            current_app.logger.exception(e)

    return jsonify(data=user_to_update.serialize()), 200


@user_blueprint.route("/<uuid:user_id>/archive", methods=["POST"])
def archive_user(user_id):
    user = get_user_by_id(user_id)
    dao_archive_user(user)

    return "", 204


@user_blueprint.route("/<uuid:user_id>/activate", methods=["POST"])
def activate_user(user_id):
    user = get_user_by_id(user_id=user_id)
    if user.state == "active":
        raise InvalidRequest("User already active", status_code=400)

    user.state = "active"
    save_model_user(user)

    if current_app.config["FF_SALESFORCE_CONTACT"]:
        salesforce_client.contact_create(user)

    return jsonify(data=user.serialize()), 200


@user_blueprint.route("/<uuid:user_id>/reset-failed-login-count", methods=["POST"])
def user_reset_failed_login_count(user_id):
    user_to_update = get_user_by_id(user_id=user_id)
    reset_failed_login_count(user_to_update)
    return jsonify(data=user_to_update.serialize()), 200


@user_blueprint.route("/<uuid:user_id>/verify/password", methods=["POST"])
def verify_user_password(user_id):
    user_to_verify = get_user_by_id(user_id=user_id)
    data = request.get_json()
    try:
        txt_pwd = data["password"]
    except KeyError:
        message = "Required field missing data"
        errors = {"password": [message]}
        raise InvalidRequest(errors, status_code=400)

    if user_to_verify.check_password(txt_pwd):
        reset_failed_login_count(user_to_verify)
        if "loginData" in data and data["loginData"] != {}:
            save_login_event(LoginEvent(user_id=user_id, data=data["loginData"]))
        return jsonify({}), 204
    else:
        increment_failed_login_count(user_to_verify)
        message = "Incorrect password"
        errors = {"password": [message]}
        raise InvalidRequest(errors, status_code=400)


@user_blueprint.route("/<uuid:user_id>/verify/code", methods=["POST"])
def verify_user_code(user_id):
    data = request.get_json()
    validate(data, post_verify_code_schema)

    user_to_verify = get_user_by_id(user_id=user_id)

    code = get_user_code(user_to_verify, data["code"], data["code_type"])

    if verify_within_time(user_to_verify) >= 2:
        raise InvalidRequest("Code already sent", status_code=400)

    if user_to_verify.failed_login_count >= current_app.config.get("MAX_VERIFY_CODE_COUNT"):
        raise InvalidRequest("Code not found", status_code=404)
    if not code:
        increment_failed_login_count(user_to_verify)
        raise InvalidRequest("Code not found", status_code=404)
    if datetime.utcnow() > code.expiry_datetime:
        # sms and email
        increment_failed_login_count(user_to_verify)
        raise InvalidRequest("Code has expired", status_code=400)
    if code.code_used:
        increment_failed_login_count(user_to_verify)
        raise InvalidRequest("Code has already been used", status_code=400)

    user_to_verify.current_session_id = str(uuid.uuid4())
    user_to_verify.logged_in_at = datetime.utcnow()
    user_to_verify.failed_login_count = 0
    save_model_user(user_to_verify)

    use_user_code(code.id)
    return jsonify({}), 204


@user_blueprint.route("/<uuid:user_id>/<code_type>-code", methods=["POST"])
def send_user_2fa_code(user_id, code_type):
    user_to_send_to = get_user_by_id(user_id=user_id)

    if count_user_verify_codes(user_to_send_to) >= current_app.config.get("MAX_VERIFY_CODE_COUNT"):
        # Prevent more than `MAX_VERIFY_CODE_COUNT` active verify codes at a time
        current_app.logger.warning("Too many verify codes created for user {}".format(user_to_send_to.id))
    elif verify_within_time(user_to_send_to, age=timedelta(seconds=10)) >= 1:
        current_app.logger.warning(f"A code has already been created for user {user_to_send_to.id} in the last 10 seconds.")
    else:
        data = request.get_json()
        if code_type == SMS_TYPE:
            validate(data, post_send_user_sms_code_schema)
            send_user_sms_code(user_to_send_to, data)
        elif code_type == EMAIL_TYPE:
            validate(data, post_send_user_email_code_schema)
            send_user_email_code(user_to_send_to, data)
        else:
            abort(404)

    return "{}", 204


def send_user_sms_code(user_to_send_to, data):
    recipient = data.get("to") or user_to_send_to.mobile_number

    secret_code = create_secret_code()
    personalisation = {"verify_code": secret_code}

    create_2fa_code(
        current_app.config["SMS_CODE_TEMPLATE_ID"],
        user_to_send_to,
        secret_code,
        recipient,
        personalisation,
    )


def send_user_email_code(user_to_send_to, data):
    recipient = user_to_send_to.email_address

    secret_code = create_secret_code()

    personalisation = {"name": user_to_send_to.name, "verify_code": secret_code}

    create_2fa_code(
        current_app.config["EMAIL_2FA_TEMPLATE_ID"],
        user_to_send_to,
        secret_code,
        recipient,
        personalisation,
    )


def create_2fa_code(template_id, user_to_send_to, secret_code, recipient, personalisation):
    template = dao_get_template_by_id(template_id)

    # save the code in the VerifyCode table
    create_user_code(user_to_send_to, secret_code, template.template_type)
    reply_to = None
    if template.template_type == SMS_TYPE:
        reply_to = template.service.get_default_sms_sender()
    elif template.template_type == EMAIL_TYPE:
        reply_to = template.service.get_default_reply_to_email_address()

    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=recipient,
        service=template.service,
        personalisation=personalisation,
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=reply_to,
    )
    # Assume that we never want to observe the Notify service's research mode
    # setting for this notification - we still need to be able to log into the
    # admin even if we're doing user research using this service:
    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)


@user_blueprint.route("/<uuid:user_id>/change-email-verification", methods=["POST"])
def send_user_confirm_new_email(user_id):
    user_to_send_to = get_user_by_id(user_id=user_id)
    email, errors = email_data_request_schema.load(request.get_json())
    if errors:
        raise InvalidRequest(message=errors, status_code=400)

    template = dao_get_template_by_id(current_app.config["CHANGE_EMAIL_CONFIRMATION_TEMPLATE_ID"])
    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])

    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=email["email"],
        service=service,
        personalisation={
            "name": user_to_send_to.name,
            "url": _create_confirmation_url(user=user_to_send_to, email_address=email["email"]),
            "feedback_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
        },
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=service.get_default_reply_to_email_address(),
    )

    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)
    return jsonify({}), 204


@user_blueprint.route("/<uuid:user_id>/email-verification", methods=["POST"])
def send_new_user_email_verification(user_id):
    # when registering, we verify all users' email addresses using this function
    user_to_send_to = get_user_by_id(user_id=user_id)

    template = dao_get_template_by_id(current_app.config["NEW_USER_EMAIL_VERIFICATION_TEMPLATE_ID"])
    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])

    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=user_to_send_to.email_address,
        service=service,
        personalisation={
            "name": user_to_send_to.name,
            "url": _create_verification_url(user_to_send_to),
        },
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=service.get_default_reply_to_email_address(),
    )

    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)

    return jsonify({}), 204


@user_blueprint.route("/<uuid:user_id>/email-already-registered", methods=["POST"])
def send_already_registered_email(user_id):
    to, errors = email_data_request_schema.load(request.get_json())
    template = dao_get_template_by_id(current_app.config["ALREADY_REGISTERED_EMAIL_TEMPLATE_ID"])
    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])

    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=to["email"],
        service=service,
        personalisation={
            "signin_url": f"{current_app.config['ADMIN_BASE_URL']}/sign-in",
            "forgot_password_url": f"{current_app.config['ADMIN_BASE_URL']}/forgot-password",
            "feedback_url": f"{current_app.config['ADMIN_BASE_URL']}/contact",
        },
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=service.get_default_reply_to_email_address(),
    )

    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)

    return jsonify({}), 204


@user_blueprint.route("/<uuid:user_id>/contact-request", methods=["POST"])
def send_contact_request(user_id):

    contact = None
    user = None

    try:
        contact = ContactRequest(**request.json)
        user = get_user_by_email(contact.email_address)
        # If the user has no live services, don't want to escalate the ticket.
        if all([s.restricted for s in user.services]):
            contact.tags = ["z_skip_opsgenie", "z_skip_urgent_escalation"]

    except TypeError as e:
        current_app.logger.error(e)
        return jsonify({}), 400
    except NoResultFound:
        # This is perfectly normal if get_user_by_email raises
        pass

    # Update the engagement stage in Salesforce for go live requests
    if contact and contact.is_go_live_request() and current_app.config["FF_SALESFORCE_CONTACT"]:
        try:
            engagement_updates = {"StageName": ENGAGEMENT_STAGE_ACTIVATION, "Description": contact.main_use_case}
            service = dao_fetch_service_by_id(contact.service_id)
            salesforce_client.engagement_update(service, user, engagement_updates)

            if not service.organisation_notes:
                # the service was created before we started requesting the organisation name at creation time
                if not contact.department_org_name:
                    # this shouldn't happen, but if it does, we don't want to leave the service with no organisation name
                    contact.department_org_name = "Unknown"
                # fall back on the organisation name collected from the go live request
                service.organisation_notes = contact.department_org_name
                dao_update_service(service)
            else:
                # this is the normal case, where the service has an organisation name collected when it was created
                contact.department_org_name = service.organisation_notes

        except Exception as e:
            current_app.logger.exception(e)

    status_code = Freshdesk(contact).send_ticket()
    return jsonify({"status_code": status_code}), 204


@user_blueprint.route("/<uuid:user_id>/branding-request", methods=["POST"])
def send_branding_request(user_id):

    contact = None
    data = request.json
    try:
        user = get_user_by_id(user_id=user_id)
        contact = ContactRequest(
            support_type="branding_request",
            friendly_support_type="Branding request",
            name=user.name,
            email_address=user.email_address,
            service_id=data["serviceID"],
            service_name=data["service_name"],
            branding_url=get_logo_url(data["filename"]),
        )
        contact.tags = ["z_skip_opsgenie", "z_skip_urgent_escalation"]

    except TypeError as e:
        current_app.logger.error(e)
        return jsonify({}), 400
    except NoResultFound as e:
        # This means that get_user_by_id couldn't find a user
        current_app.logger.error(e)
        return jsonify({}), 400

    status_code = Freshdesk(contact).send_ticket()
    return jsonify({"status_code": status_code}), 204


@user_blueprint.route("/<uuid:user_id>", methods=["GET"])
@user_blueprint.route("", methods=["GET"])
def get_user(user_id=None):
    users = get_user_by_id(user_id=user_id)
    result = [x.serialize() for x in users] if isinstance(users, list) else users.serialize()
    return jsonify(data=result)


@user_blueprint.route("/<uuid:user_id>/service/<uuid:service_id>/permission", methods=["POST"])
def set_permissions(user_id, service_id):
    # TODO fix security hole, how do we verify that the user
    # who is making this request has permission to make the request.
    service_user = dao_get_service_user(user_id, service_id)
    user = get_user_by_id(user_id)
    service = dao_fetch_service_by_id(service_id=service_id)

    data = request.get_json()
    validate(data, post_set_permissions_schema)

    permission_list = [
        Permission(service_id=service_id, user_id=user_id, permission=p["permission"]) for p in data["permissions"]
    ]

    service_key = "service_id_{}".format(service_id)
    change_dict = {service_key: service_id, "permissions": permission_list}

    try:
        _update_alert(user, change_dict)
    except Exception as e:
        current_app.logger.error(e)

    permission_dao.set_user_service_permission(user, service, permission_list, _commit=True, replace=True)

    if "folder_permissions" in data:
        folders = [
            dao_get_template_folder_by_id_and_service_id(folder_id, service_id) for folder_id in data["folder_permissions"]
        ]

        service_user.folders = folders
        dao_update_service_user(service_user)

    return jsonify({}), 204


@user_blueprint.route("/email", methods=["GET"])
def get_by_email():
    email = request.args.get("email")
    if not email:
        error = "Invalid request. Email query string param required"
        raise InvalidRequest(error, status_code=400)
    fetched_user = get_user_by_email(email)
    result = fetched_user.serialize()
    return jsonify(data=result)


@user_blueprint.route("/find-users-by-email", methods=["POST"])
def find_users_by_email():
    email, errors = partial_email_data_request_schema.load(request.get_json())
    fetched_users = get_users_by_partial_email(email["email"])
    result = [user.serialize_for_users_list() for user in fetched_users]
    return jsonify(data=result), 200


@user_blueprint.route("/reset-password", methods=["POST"])
def send_user_reset_password():
    email, errors = email_data_request_schema.load(request.get_json())

    user_to_send_to = get_user_by_email(email["email"])

    if user_to_send_to.blocked:
        return jsonify({"message": "cannot reset password: user blocked"}), 400

    template = dao_get_template_by_id(current_app.config["PASSWORD_RESET_TEMPLATE_ID"])
    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])
    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=email["email"],
        service=service,
        personalisation={
            "user_name": user_to_send_to.name,
            "url": _create_reset_password_url(user_to_send_to.email_address),
        },
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=service.get_default_reply_to_email_address(),
    )

    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)

    return jsonify({}), 204


@user_blueprint.route("/forced-password-reset", methods=["POST"])
def send_forced_user_reset_password():
    email, errors = email_data_request_schema.load(request.get_json())

    user_to_send_to = get_user_by_email(email["email"])

    if user_to_send_to.blocked:
        return jsonify({"message": "cannot reset password: user blocked"}), 400

    template = dao_get_template_by_id(current_app.config["FORCED_PASSWORD_RESET_TEMPLATE_ID"])
    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])
    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=email["email"],
        service=service,
        personalisation={
            "user_name": user_to_send_to.name,
            "url": _create_reset_password_url(user_to_send_to.email_address),
        },
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=service.get_default_reply_to_email_address(),
    )

    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)

    return jsonify({}), 204


@user_blueprint.route("/<uuid:user_id>/update-password", methods=["POST"])
def update_password(user_id):
    user = get_user_by_id(user_id=user_id)
    req_json = request.get_json()
    pwd = req_json.get("_password")
    alert_user = req_json.get("alert_user", True)

    login_data = {}

    if "loginData" in req_json:
        login_data = req_json["loginData"]
        del req_json["loginData"]

    update_dct, errors = user_update_password_schema_load_json.load(req_json)
    if errors:
        raise InvalidRequest(errors, status_code=400)

    response = pwnedpasswords.check(pwd)
    if response > 0:
        errors.update({"password": ["Password is not allowed."]})
        raise InvalidRequest(errors, status_code=400)

    update_user_password(user, pwd)

    # save login event
    if login_data:
        save_login_event(LoginEvent(user_id=user.id, data=login_data))

    changes = {"password": "password updated"}

    if alert_user:
        try:
            _update_alert(user, changes)
        except Exception as e:
            current_app.logger.error(e)

    return jsonify(data=user.serialize()), 200


@user_blueprint.route("/<uuid:user_id>/organisations-and-services", methods=["GET"])
def get_organisations_and_services_for_user(user_id):
    user = get_user_and_accounts(user_id)
    data = get_orgs_and_services(user)
    return jsonify(data)


@user_blueprint.route("/<uuid:user_id>/fido2_keys", methods=["GET"])
def list_fido2_keys_user(user_id):
    data = list_fido2_keys(user_id)
    return jsonify(list(map(lambda o: o.serialize(), data)))


@user_blueprint.route("/<uuid:user_id>/fido2_keys", methods=["POST"])
def create_fido2_keys_user(user_id):
    user = get_user_and_accounts(user_id)
    data = request.get_json()
    cbor_data = cbor.decode(base64.b64decode(data["payload"]))
    validate(data, fido2_key_schema)

    id = uuid.uuid4()
    key = decode_and_register(cbor_data, get_fido2_session(user_id))
    save_fido2_key(Fido2Key(id=id, user_id=user_id, name=cbor_data["name"], key=key))
    _update_alert(user, changes={"security_key_created": None})
    return jsonify({"id": id})


@user_blueprint.route("/<uuid:user_id>/fido2_keys/register", methods=["POST"])
def fido2_keys_user_register(user_id):
    user = get_user_and_accounts(user_id)
    keys = list_fido2_keys(user_id)

    credentials = list(map(lambda k: pickle.loads(base64.b64decode(k.key)), keys))

    registration_data, state = Config.FIDO2_SERVER.register_begin(
        {
            "id": user.id.bytes,
            "name": user.name,
            "displayName": user.name,
        },
        credentials,
        user_verification="discouraged",
    )
    create_fido2_session(user_id, state)

    # API Client only like JSON
    return jsonify({"data": base64.b64encode(cbor.encode(registration_data)).decode("utf8")})


@user_blueprint.route("/<uuid:user_id>/fido2_keys/authenticate", methods=["POST"])
def fido2_keys_user_authenticate(user_id):
    keys = list_fido2_keys(user_id)
    credentials = list(map(lambda k: pickle.loads(base64.b64decode(k.key)), keys))

    auth_data, state = Config.FIDO2_SERVER.authenticate_begin(credentials)
    create_fido2_session(user_id, state)

    # API Client only like JSON
    return jsonify({"data": base64.b64encode(cbor.encode(auth_data)).decode("utf8")})


@user_blueprint.route("/<uuid:user_id>/fido2_keys/validate", methods=["POST"])
def fido2_keys_user_validate(user_id):
    keys = list_fido2_keys(user_id)
    credentials = list(map(lambda k: pickle.loads(base64.b64decode(k.key)), keys))

    data = request.get_json()
    cbor_data = cbor.decode(base64.b64decode(data["payload"]))

    credential_id = cbor_data["credentialId"]
    client_data = ClientData(cbor_data["clientDataJSON"])
    auth_data = AuthenticatorData(cbor_data["authenticatorData"])
    signature = cbor_data["signature"]

    Config.FIDO2_SERVER.authenticate_complete(
        get_fido2_session(user_id),
        credentials,
        credential_id,
        client_data,
        auth_data,
        signature,
    )

    user_to_verify = get_user_by_id(user_id=user_id)
    user_to_verify.current_session_id = str(uuid.uuid4())
    user_to_verify.logged_in_at = datetime.utcnow()
    user_to_verify.failed_login_count = 0
    save_model_user(user_to_verify)

    return jsonify({"status": "OK"})


@user_blueprint.route("/<uuid:user_id>/fido2_keys/<uuid:key_id>", methods=["DELETE"])
def delete_fido2_keys_user(user_id, key_id):
    user = get_user_and_accounts(user_id)
    delete_fido2_key(user_id, key_id)
    _update_alert(user, changes={"security_key_deleted": None})
    return jsonify({"id": key_id})


@user_blueprint.route("/<uuid:user_id>/login_events", methods=["GET"])
def list_login_events_user(user_id):
    data = list_login_events(user_id)
    return jsonify(list(map(lambda o: o.serialize(), data)))


def _create_reset_password_url(email):
    data = json.dumps({"email": email, "created_at": str(datetime.utcnow())})
    url = "/new-password/"
    return url_with_token(data, url, current_app.config)


def _create_verification_url(user):
    data = json.dumps({"user_id": str(user.id), "email": user.email_address})
    url = "/verify-email/"
    return url_with_token(data, url, current_app.config)


def _create_confirmation_url(user, email_address):
    data = json.dumps({"user_id": str(user.id), "email": email_address})
    url = "/user-profile/email/confirm/"
    return url_with_token(data, url, current_app.config)


def get_orgs_and_services(user):
    return {
        "organisations": [
            {
                "name": org.name,
                "id": org.id,
                "count_of_live_services": len(org.live_services),
            }
            for org in user.organisations
            if org.active
        ],
        "services": [
            {
                "id": service.id,
                "name": service.name,
                "restricted": service.restricted,
                "organisation": service.organisation.id if service.organisation else None,
            }
            for service in user.services
            if service.active
        ],
    }


def _update_alert(user_to_update, changes=None):
    service = Service.query.get(current_app.config["NOTIFY_SERVICE_ID"])
    template = dao_get_template_by_id(current_app.config["ACCOUNT_CHANGE_TEMPLATE_ID"])
    recipient = user_to_update.email_address
    reply_to = template.service.get_default_reply_to_email_address()

    change_type_en = ""
    change_type_fr = ""
    if changes:
        change_type_en = update_dct_to_str(changes, "EN")
        change_type_fr = update_dct_to_str(changes, "FR")

    saved_notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=recipient,
        service=service,
        personalisation={
            "base_url": Config.ADMIN_BASE_URL,
            "contact_us_url": f"{Config.ADMIN_BASE_URL}/contact",
            "change_type_en": change_type_en,
            "change_type_fr": change_type_fr,
        },
        notification_type=template.template_type,
        api_key_id=None,
        key_type=KEY_TYPE_NORMAL,
        reply_to_text=reply_to,
    )

    send_notification_to_queue(saved_notification, False, queue=QueueNames.NOTIFY)
