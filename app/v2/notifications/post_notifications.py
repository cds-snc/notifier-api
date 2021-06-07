import base64
import csv
import functools
from io import StringIO

import werkzeug
from flask import abort, current_app, jsonify, request
from notifications_utils.recipients import (
    RecipientCSV,
    try_validate_and_format_phone_number,
)

from app import (
    api_user,
    authenticated_service,
    document_download_client,
    notify_celery,
    statsd_client,
)
from app.celery.letters_pdf_tasks import create_letters_pdf, process_virus_scan_passed
from app.celery.research_mode_tasks import create_fake_letter_response_file
from app.clients.document_download import DocumentDownloadError
from app.config import QueueNames, TaskNames
from app.dao.notifications_dao import update_notification_status_by_reference
from app.dao.services_dao import fetch_todays_total_message_count
from app.dao.templates_dao import get_precompiled_letter_template
from app.letters.utils import upload_letter_pdf
from app.models import (
    EMAIL_TYPE,
    KEY_TYPE_TEAM,
    KEY_TYPE_TEST,
    LETTER_TYPE,
    NOTIFICATION_CREATED,
    NOTIFICATION_DELIVERED,
    NOTIFICATION_PENDING_VIRUS_CHECK,
    NOTIFICATION_SENDING,
    SMS_TYPE,
    UPLOAD_DOCUMENT,
)
from app.notifications.process_letter_notifications import create_letter_notification
from app.notifications.process_notifications import (
    persist_notification,
    persist_scheduled_notification,
    send_notification_to_queue,
    simulated_recipient,
)
from app.notifications.validators import (
    check_rate_limiting,
    check_service_can_schedule_notification,
    check_service_email_reply_to_id,
    check_service_has_permission,
    check_service_sms_sender_id,
    validate_and_format_recipient,
    validate_template,
    validate_template_exists,
)
from app.schema_validation import validate
from app.service.utils import safelisted_members
from app.v2.errors import BadRequestError
from app.v2.notifications import v2_notification_blueprint
from app.v2.notifications.create_response import (
    create_post_email_response_from_notification,
    create_post_letter_response_from_notification,
    create_post_sms_response_from_notification,
)
from app.v2.notifications.notification_schemas import (
    post_bulk_request,
    post_email_request,
    post_letter_request,
    post_precompiled_letter_request,
    post_sms_request,
)


@v2_notification_blueprint.route("/{}".format(LETTER_TYPE), methods=["POST"])
def post_precompiled_letter_notification():
    if "content" not in (request.get_json() or {}):
        return post_notification(LETTER_TYPE)

    form = validate(request.get_json(), post_precompiled_letter_request)

    # Check permission to send letters
    check_service_has_permission(LETTER_TYPE, authenticated_service.permissions)

    check_rate_limiting(authenticated_service, api_user)

    template = get_precompiled_letter_template(authenticated_service.id)

    form["personalisation"] = {"address_line_1": form["reference"]}

    reply_to = get_reply_to_text(LETTER_TYPE, form, template)

    notification = process_letter_notification(
        letter_data=form,
        api_key=api_user,
        template=template,
        reply_to_text=reply_to,
        precompiled=True,
    )

    resp = {
        "id": notification.id,
        "reference": notification.client_reference,
        "postage": notification.postage,
    }

    return jsonify(resp), 201


@v2_notification_blueprint.route("/bulk", methods=["POST"])
def post_bulk():
    try:
        request_json = request.get_json()
    except werkzeug.exceptions.BadRequest as e:
        raise BadRequestError(
            message="Error decoding arguments: {}".format(e.description),
            status_code=400,
        )

    max_rows = current_app.config["CSV_MAX_ROWS"]
    form = validate(request_json, post_bulk_request(max_rows))

    if len([source for source in [form.get("rows"), form.get("csv")] if source]) != 1:
        raise BadRequestError(
            message="You should specify either rows or csv",
            status_code=400,
        )
    template = validate_template_exists(form["template_id"], authenticated_service)
    check_service_has_permission(template.template_type, authenticated_service.permissions)

    sender_id = get_reply_to_text(template.template_type, form, template, form_field="reply_to_id")
    remaining_messages = authenticated_service.message_limit - fetch_todays_total_message_count(authenticated_service.id)

    try:
        if form.get("rows"):
            output = StringIO()
            writer = csv.writer(output)
            writer.writerows(form["rows"])
            file_data = output.getvalue()
        else:
            file_data = form["csv"]

        recipient_csv = RecipientCSV(
            file_data,
            template_type=template.template_type,
            placeholders=template._as_utils_template().placeholders,
            max_rows=max_rows,
            safelist=safelisted_members(authenticated_service, api_user.key_type),
            remaining_messages=remaining_messages,
        )
    except csv.Error as e:
        raise BadRequestError(message=f"Error converting to CSV: {str(e)}", status_code=400)

    check_for_csv_errors(recipient_csv, max_rows, remaining_messages)

    return f"OK {sender_id}", 200


@v2_notification_blueprint.route("/<notification_type>", methods=["POST"])
def post_notification(notification_type):
    try:
        request_json = request.get_json()
    except werkzeug.exceptions.BadRequest as e:
        raise BadRequestError(
            message="Error decoding arguments: {}".format(e.description),
            status_code=400,
        )

    if notification_type == EMAIL_TYPE:
        form = validate(request_json, post_email_request)
    elif notification_type == SMS_TYPE:
        form = validate(request_json, post_sms_request)
    elif notification_type == LETTER_TYPE:
        form = validate(request_json, post_letter_request)
    else:
        abort(404)

    check_service_has_permission(notification_type, authenticated_service.permissions)

    scheduled_for = form.get("scheduled_for", None)

    check_service_can_schedule_notification(authenticated_service.permissions, scheduled_for)

    check_rate_limiting(authenticated_service, api_user)

    template, template_with_content = validate_template(
        form["template_id"],
        strip_keys_from_personalisation_if_send_attach(form.get("personalisation", {})),
        authenticated_service,
        notification_type,
    )

    reply_to = get_reply_to_text(notification_type, form, template)

    if notification_type == LETTER_TYPE:
        notification = process_letter_notification(
            letter_data=form,
            api_key=api_user,
            template=template,
            reply_to_text=reply_to,
        )
    else:
        notification = process_sms_or_email_notification(
            form=form,
            notification_type=notification_type,
            api_key=api_user,
            template=template,
            service=authenticated_service,
            reply_to_text=reply_to,
        )

        template_with_content.values = notification.personalisation

    if notification_type == SMS_TYPE:
        create_resp_partial = functools.partial(create_post_sms_response_from_notification, from_number=reply_to)
    elif notification_type == EMAIL_TYPE:
        if authenticated_service.sending_domain is None or authenticated_service.sending_domain.strip() == "":
            sending_domain = current_app.config["NOTIFY_EMAIL_DOMAIN"]
        else:
            sending_domain = authenticated_service.sending_domain
        create_resp_partial = functools.partial(
            create_post_email_response_from_notification,
            subject=template_with_content.subject,
            email_from="{}@{}".format(authenticated_service.email_from, sending_domain),
        )
    elif notification_type == LETTER_TYPE:
        create_resp_partial = functools.partial(
            create_post_letter_response_from_notification,
            subject=template_with_content.subject,
        )

    resp = create_resp_partial(
        notification=notification,
        content=str(template_with_content),
        url_root=request.url_root,
        scheduled_for=scheduled_for,
    )
    return jsonify(resp), 201


def process_sms_or_email_notification(*, form, notification_type, api_key, template, service, reply_to_text=None):
    form_send_to = form["email_address"] if notification_type == EMAIL_TYPE else form["phone_number"]

    send_to = validate_and_format_recipient(
        send_to=form_send_to,
        key_type=api_key.key_type,
        service=service,
        notification_type=notification_type,
    )

    # Do not persist or send notification to the queue if it is a simulated recipient
    simulated = simulated_recipient(send_to, notification_type)

    personalisation = process_document_uploads(form.get("personalisation"), service, simulated, template.id)

    notification = persist_notification(
        template_id=template.id,
        template_version=template.version,
        recipient=form_send_to,
        service=service,
        personalisation=personalisation,
        notification_type=notification_type,
        api_key_id=api_key.id,
        key_type=api_key.key_type,
        client_reference=form.get("reference", None),
        simulated=simulated,
        reply_to_text=reply_to_text,
    )

    scheduled_for = form.get("scheduled_for", None)
    if scheduled_for:
        persist_scheduled_notification(notification.id, form["scheduled_for"])
    else:
        if not simulated:
            send_notification_to_queue(
                notification=notification,
                research_mode=service.research_mode,
                queue=template.queue_to_use(),
            )
        else:
            current_app.logger.debug("POST simulated notification for id: {}".format(notification.id))

    return notification


def process_document_uploads(personalisation_data, service, simulated, template_id):
    file_keys = [k for k, v in (personalisation_data or {}).items() if isinstance(v, dict) and "file" in v]
    if not file_keys:
        return personalisation_data

    personalisation_data = personalisation_data.copy()

    check_service_has_permission(UPLOAD_DOCUMENT, authenticated_service.permissions)

    for key in file_keys:
        if simulated:
            personalisation_data[key] = document_download_client.get_upload_url(service.id) + "/test-document"
        else:
            try:
                personalisation_data[key] = document_download_client.upload_document(service.id, personalisation_data[key])
            except DocumentDownloadError as e:
                raise BadRequestError(message=e.message, status_code=e.status_code)

    if not simulated:
        save_stats_for_attachments(
            [v for k, v in personalisation_data.items() if k in file_keys],
            service.id,
            template_id,
        )

    return personalisation_data


def save_stats_for_attachments(files_data, service_id, template_id):
    nb_files = len(files_data)
    statsd_client.incr(f"attachments.nb-attachments.count-{nb_files}")
    statsd_client.incr("attachments.nb-attachments", count=nb_files)
    statsd_client.incr(f"attachments.services.{service_id}", count=nb_files)
    statsd_client.incr(f"attachments.templates.{template_id}", count=nb_files)

    for document in [f["document"] for f in files_data]:
        statsd_client.incr(f"attachments.sending-method.{document['sending_method']}")
        statsd_client.incr(f"attachments.file-type.{document['mime_type']}")
        # File size is in bytes, convert to whole megabytes
        nb_mb = document["file_size"] // (1_024 * 1_024)
        file_size_bucket = f"{nb_mb}-{nb_mb+1}mb"
        statsd_client.incr(f"attachments.file-size.{file_size_bucket}")


def process_letter_notification(*, letter_data, api_key, template, reply_to_text, precompiled=False):
    if api_key.key_type == KEY_TYPE_TEAM:
        raise BadRequestError(message="Cannot send letters with a team api key", status_code=403)

    if not api_key.service.research_mode and api_key.service.restricted and api_key.key_type != KEY_TYPE_TEST:
        raise BadRequestError(message="Cannot send letters when service is in trial mode", status_code=403)

    if precompiled:
        return process_precompiled_letter_notifications(
            letter_data=letter_data,
            api_key=api_key,
            template=template,
            reply_to_text=reply_to_text,
        )

    test_key = api_key.key_type == KEY_TYPE_TEST

    # if we don't want to actually send the letter, then start it off in SENDING so we don't pick it up
    status = NOTIFICATION_CREATED if not test_key else NOTIFICATION_SENDING
    queue = QueueNames.CREATE_LETTERS_PDF if not test_key else QueueNames.RESEARCH_MODE

    notification = create_letter_notification(
        letter_data=letter_data,
        template=template,
        api_key=api_key,
        status=status,
        reply_to_text=reply_to_text,
    )

    create_letters_pdf.apply_async([str(notification.id)], queue=queue)

    if test_key:
        if current_app.config["NOTIFY_ENVIRONMENT"] in ["preview", "development"]:
            create_fake_letter_response_file.apply_async((notification.reference,), queue=queue)
        else:
            update_notification_status_by_reference(notification.reference, NOTIFICATION_DELIVERED)

    return notification


def process_precompiled_letter_notifications(*, letter_data, api_key, template, reply_to_text):
    try:
        status = NOTIFICATION_PENDING_VIRUS_CHECK
        letter_content = base64.b64decode(letter_data["content"])
    except ValueError:
        raise BadRequestError(
            message="Cannot decode letter content (invalid base64 encoding)",
            status_code=400,
        )

    notification = create_letter_notification(
        letter_data=letter_data,
        template=template,
        api_key=api_key,
        status=status,
        reply_to_text=reply_to_text,
    )

    filename = upload_letter_pdf(notification, letter_content, precompiled=True)

    current_app.logger.info("Calling task scan-file for {}".format(filename))

    # call task to add the filename to anti virus queue
    if current_app.config["ANTIVIRUS_ENABLED"]:
        notify_celery.send_task(
            name=TaskNames.SCAN_FILE,
            kwargs={"filename": filename},
            queue=QueueNames.ANTIVIRUS,
        )
    else:
        # stub out antivirus in dev
        process_virus_scan_passed.apply_async(
            kwargs={"filename": filename},
            queue=QueueNames.LETTERS,
        )

    return notification


def get_reply_to_text(notification_type, form, template, form_field=None):
    reply_to = None
    if notification_type == EMAIL_TYPE:
        service_email_reply_to_id = form.get(form_field or "email_reply_to_id")
        reply_to = (
            check_service_email_reply_to_id(
                str(authenticated_service.id),
                service_email_reply_to_id,
                notification_type,
            )
            or template.get_reply_to_text()
        )

    elif notification_type == SMS_TYPE:
        service_sms_sender_id = form.get(form_field or "sms_sender_id")
        sms_sender_id = check_service_sms_sender_id(str(authenticated_service.id), service_sms_sender_id, notification_type)
        if sms_sender_id:
            reply_to = try_validate_and_format_phone_number(sms_sender_id)
        else:
            reply_to = template.get_reply_to_text()

    elif notification_type == LETTER_TYPE:
        reply_to = template.get_reply_to_text()

    return reply_to


def strip_keys_from_personalisation_if_send_attach(personalisation):
    return {k: v for (k, v) in personalisation.items() if not (type(v) is dict and v.get("sending_method") == "attach")}


def check_for_csv_errors(recipient_csv, max_rows, remaining_messages):
    nb_rows = len(recipient_csv)

    if recipient_csv.has_errors:
        if recipient_csv.missing_column_headers:
            raise BadRequestError(
                message=f"Missing column headers: {', '.join(sorted(recipient_csv.missing_column_headers))}",
                status_code=400,
            )
        if recipient_csv.duplicate_recipient_column_headers:
            raise BadRequestError(
                message=f"Duplicate column headers: {', '.join(sorted(recipient_csv.duplicate_recipient_column_headers))}",
                status_code=400,
            )
        if recipient_csv.more_rows_than_can_send:
            raise BadRequestError(
                message=f"You can only send up to {remaining_messages} remaining messages today and you tried to send {nb_rows} messages.",
                status_code=400,
            )

        if recipient_csv.too_many_rows:
            raise BadRequestError(
                message=f"Too many rows. Maximum number of rows allowed is {max_rows}",
                status_code=400,
            )
        if not recipient_csv.allowed_to_send_to:
            if api_user.key_type == KEY_TYPE_TEAM:
                explanation = "because you used a team and safelist API key."
            if authenticated_service.restricted:
                explanation = (
                    "because your service is in trial mode and you can only send to members of your team and your safelist."
                )
            raise BadRequestError(
                message=f"You're not allowed to send to these recipients {explanation}",
                status_code=400,
            )
        if recipient_csv.rows_with_errors:

            def row_error(row):
                res = []
                for header in [header for header in recipient_csv.column_headers if row[header].error]:
                    if row[header].recipient_error:
                        res.append(f"`{header}`: invalid recipient")
                    else:
                        res.append(f"`{header}`: {row[header].error}")
                return f"Row {row.index} - {','.join(res)}"

            errors = ". ".join([row_error(row) for row in recipient_csv.initial_rows_with_errors])
            raise BadRequestError(
                message=f"Some rows have errors. {errors}.",
                status_code=400,
            )
        else:
            raise NotImplementedError(f"Got errors but code did not handle")
