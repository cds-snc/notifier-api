from datetime import datetime

import dateutil
from flask import Blueprint, current_app, jsonify, request
from notifications_utils.recipients import RecipientCSV
from notifications_utils.template import Template

from app.aws.s3 import get_job_from_s3, get_job_metadata_from_s3
from app.celery.tasks import process_job
from app.config import QueueNames
from app.dao.fact_notification_status_dao import fetch_notification_statuses_for_job
from app.dao.jobs_dao import (
    can_letter_job_be_cancelled,
    dao_cancel_letter_job,
    dao_create_job,
    dao_get_future_scheduled_job_by_id_and_service_id,
    dao_get_job_by_service_id_and_job_id,
    dao_get_jobs_by_service_id,
    dao_get_notification_outcomes_for_job,
    dao_update_job,
)
from app.dao.notifications_dao import get_notifications_for_job
from app.dao.services_dao import dao_fetch_service_by_id
from app.dao.templates_dao import dao_get_template_by_id
from app.email_limit_utils import decrement_todays_email_count
from app.errors import InvalidRequest, register_errors
from app.models import (
    EMAIL_TYPE,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_PENDING,
    JOB_STATUS_SCHEDULED,
    SMS_TYPE,
)
from app.notifications.process_notifications import simulated_recipient
from app.notifications.validators import (
    check_email_daily_limit,
    check_sms_daily_limit,
    increment_email_daily_count_send_warnings_if_needed,
    increment_sms_daily_count_send_warnings_if_needed,
)
from app.schemas import (
    job_schema,
    notification_with_template_schema,
    notifications_filter_schema,
    unarchived_template_schema,
)
from app.utils import midnight_n_days_ago, pagination_links

job_blueprint = Blueprint("job", __name__, url_prefix="/service/<uuid:service_id>/job")


register_errors(job_blueprint)


@job_blueprint.route("/<job_id>", methods=["GET"])
def get_job_by_service_and_job_id(service_id, job_id):
    job = dao_get_job_by_service_id_and_job_id(service_id, job_id)
    if job is not None:
        statistics = dao_get_notification_outcomes_for_job(service_id, job_id)
        data = job_schema.dump(job)
        data["statistics"] = [{"status": statistic[1], "count": statistic[0]} for statistic in statistics]
        return jsonify(data=data)
    else:
        current_app.logger.warning(f"Job not found in database for service_id {service_id} job_id {job_id}")
        return jsonify(result="error", message="Job not found in database"), 404


@job_blueprint.route("/<job_id>/cancel", methods=["POST"])
def cancel_job(service_id, job_id):
    job = dao_get_future_scheduled_job_by_id_and_service_id(job_id, service_id)
    job.job_status = JOB_STATUS_CANCELLED
    dao_update_job(job)
    decrement_todays_email_count(service_id, job.notification_count)
    return get_job_by_service_and_job_id(service_id, job_id)


@job_blueprint.route("/<job_id>/cancel-letter-job", methods=["POST"])
def cancel_letter_job(service_id, job_id):
    job = dao_get_job_by_service_id_and_job_id(service_id, job_id)
    if job is not None:
        can_we_cancel, errors = can_letter_job_be_cancelled(job)
        if can_we_cancel:
            data = dao_cancel_letter_job(job)
            return jsonify(data), 200
        else:
            return jsonify(message=errors), 400
    else:
        return jsonify(result="error", message="Job not found in database"), 404


@job_blueprint.route("/<job_id>/notifications", methods=["GET"])
def get_all_notifications_for_service_job(service_id, job_id):
    data = notifications_filter_schema.load(request.args)
    page = data["page"] if "page" in data else 1
    page_size = data["page_size"] if "page_size" in data else current_app.config.get("PAGE_SIZE")
    paginated_notifications = get_notifications_for_job(service_id, job_id, filter_dict=data, page=page, page_size=page_size)

    kwargs = request.args.to_dict()
    kwargs["service_id"] = service_id
    kwargs["job_id"] = job_id

    notifications = None
    if data.get("format_for_csv"):
        notifications = [notification.serialize_for_csv() for notification in paginated_notifications.items]
    else:
        notifications = notification_with_template_schema.dump(paginated_notifications.items, many=True)

    return (
        jsonify(
            notifications=notifications,
            page_size=page_size,
            total=paginated_notifications.total,
            links=pagination_links(paginated_notifications, ".get_all_notifications_for_service_job", **kwargs),
        ),
        200,
    )


@job_blueprint.route("", methods=["GET"])
def get_jobs_by_service(service_id):
    if request.args.get("limit_days"):
        try:
            limit_days = int(request.args["limit_days"])
        except ValueError:
            errors = {"limit_days": ["{} is not an integer".format(request.args["limit_days"])]}
            raise InvalidRequest(errors, status_code=400)
    else:
        limit_days = None

    statuses = [x.strip() for x in request.args.get("statuses", "").split(",")]

    page = int(request.args.get("page", 1))
    return jsonify(**get_paginated_jobs(service_id, limit_days, statuses, page))


@job_blueprint.route("", methods=["POST"])
def create_job(service_id):
    service = dao_fetch_service_by_id(service_id)
    if not service.active:
        raise InvalidRequest("Create job is not allowed: service is inactive ", 403)

    data = request.get_json()
    data.update({"service": service_id})

    try:
        data.update(**get_job_metadata_from_s3(service_id, data["id"]))
    except KeyError:
        raise InvalidRequest({"id": ["Missing data for required field."]}, status_code=400)

    if data.get("valid") != "True":
        raise InvalidRequest("File is not valid, can't create job", 400)

    data["template"] = data.pop("template_id")

    template = dao_get_template_by_id(data["template"])
    template_errors = unarchived_template_schema.validate({"archived": template.archived})

    if template_errors:
        raise InvalidRequest(template_errors, status_code=400)

    job = get_job_from_s3(service_id, data["id"])
    recipient_csv = RecipientCSV(
        job,
        template_type=template.template_type,
        placeholders=template._as_utils_template().placeholders,
        template=Template(template.__dict__),
    )

    if template.template_type == SMS_TYPE:
        # calculate the number of simulated recipients
        numberOfSimulated = sum(simulated_recipient(i["phone_number"].data, template.template_type) for i in recipient_csv.rows)
        mixedRecipients = numberOfSimulated > 0 and numberOfSimulated != len(recipient_csv)

        # if they have specified testing and NON-testing recipients, raise an error
        if mixedRecipients:
            raise InvalidRequest(message="Bulk sending to testing and non-testing numbers is not supported", status_code=400)

        is_test_notification = len(recipient_csv) == numberOfSimulated

        if not is_test_notification:
            check_sms_daily_limit(service, len(recipient_csv))
            increment_sms_daily_count_send_warnings_if_needed(service, len(recipient_csv))

    elif template.template_type == EMAIL_TYPE:
        try:
            notification_count = int(data["notification_count"])
        except KeyError:
            current_app.logger.warning(
                f"notification_count not in metadata for job {data['id']}, using len(recipient_csv) instead."
            )
            notification_count = len(recipient_csv)

        check_email_daily_limit(service, notification_count)

        scheduled_for = datetime.fromisoformat(data.get("scheduled_for")) if data.get("scheduled_for") else None

        if scheduled_for is None or not scheduled_for.date() > datetime.today().date():
            increment_email_daily_count_send_warnings_if_needed(service, notification_count)

    data.update({"template_version": template.version})

    job = job_schema.load(data)

    if job.scheduled_for:
        job.job_status = JOB_STATUS_SCHEDULED

    dao_create_job(job)

    if job.job_status == JOB_STATUS_PENDING:
        process_job.apply_async([str(job.id)], queue=QueueNames.JOBS)

    job_json = job_schema.dump(job)
    job_json["statistics"] = []

    return jsonify(data=job_json), 201


def get_paginated_jobs(service_id, limit_days, statuses, page):
    pagination = dao_get_jobs_by_service_id(
        service_id,
        limit_days=limit_days,
        page=page,
        page_size=current_app.config["PAGE_SIZE"],
        statuses=statuses,
    )
    data = job_schema.dump(pagination.items, many=True)
    for job_data in data:
        start = job_data["processing_started"]
        start = dateutil.parser.parse(start).replace(tzinfo=None) if start else None

        if start is None:
            statistics = []
        elif start.replace(tzinfo=None) < midnight_n_days_ago(3):
            # ft_notification_status table
            statistics = fetch_notification_statuses_for_job(job_data["id"])
        else:
            # notifications table
            statistics = dao_get_notification_outcomes_for_job(service_id, job_data["id"])
        job_data["statistics"] = [{"status": statistic.status, "count": statistic.count} for statistic in statistics]

    return {
        "data": data,
        "page_size": pagination.per_page,
        "total": pagination.total,
        "links": pagination_links(pagination, ".get_jobs_by_service", service_id=service_id),
    }
