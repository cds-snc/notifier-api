import json
from collections import namedtuple
from datetime import datetime
from itertools import islice
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from flask import current_app
from itsdangerous import BadSignature
from more_itertools import chunked
from notifications_utils.recipients import (
    RecipientCSV,
    try_validate_and_format_phone_number,
)
from notifications_utils.statsd_decorators import statsd
from notifications_utils.template import SMSMessageTemplate, WithSubjectTemplate
from notifications_utils.timezones import convert_utc_to_local_timezone
from requests import HTTPError, RequestException, request
from sqlalchemy.exc import SQLAlchemyError

from app import (
    DATETIME_FORMAT,
    bounce_rate_client,
    create_uuid,
    email_bulk,
    email_normal,
    email_priority,
    metrics_logger,
    notify_celery,
    signer_notification,
    sms_bulk,
    sms_normal,
    sms_priority,
    statsd_client,
)
from app.aws import s3
from app.aws.metrics import (
    put_batch_saving_bulk_created,
    put_batch_saving_bulk_processed,
)
from app.config import Config, Priorities, QueueNames
from app.dao.fact_notification_status_dao import (
    fetch_notification_status_totals_for_service_by_fiscal_year,
)
from app.dao.inbound_sms_dao import dao_get_inbound_sms_by_id
from app.dao.jobs_dao import dao_get_in_progress_jobs, dao_get_job_by_id, dao_update_job
from app.dao.notifications_dao import (
    dao_get_last_notification_added_for_job_id,
    dao_get_notification_history_by_reference,
    get_latest_sent_notification_for_job,
    get_notification_by_id,
    total_hard_bounces_grouped_by_hour,
    total_notifications_grouped_by_hour,
)
from app.dao.service_email_reply_to_dao import dao_get_reply_to_by_id
from app.dao.service_inbound_api_dao import get_service_inbound_api_for_service
from app.dao.service_sms_sender_dao import dao_get_service_sms_senders_by_id
from app.dao.services_dao import dao_fetch_service_by_id
from app.dao.templates_dao import dao_get_template_by_id
from app.email_limit_utils import fetch_todays_email_count
from app.encryption import SignedNotification
from app.exceptions import DVLAException
from app.models import (
    BULK,
    EMAIL_TYPE,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FINISHED,
    JOB_STATUS_IN_PROGRESS,
    JOB_STATUS_PENDING,
    JOB_STATUS_SENDING_LIMITS_EXCEEDED,
    KEY_TYPE_NORMAL,
    LETTER_TYPE,
    NORMAL,
    PRIORITY,
    SMS_TYPE,
    Job,
    Notification,
    Service,
    Template,
)
from app.notifications.process_notifications import (
    persist_notifications,
    send_notification_to_queue,
)
from app.sms_fragment_utils import fetch_todays_requested_sms_count
from app.types import VerifiedNotification
from app.utils import get_csv_max_rows, get_delivery_queue_for_template, get_fiscal_year
from app.v2.errors import (
    LiveServiceTooManyRequestsError,
    LiveServiceTooManySMSRequestsError,
    TrialServiceTooManyRequestsError,
    TrialServiceTooManySMSRequestsError,
)


def update_in_progress_jobs():
    jobs = dao_get_in_progress_jobs()
    for job in jobs:
        notification = get_latest_sent_notification_for_job(job.id)
        if notification is not None:
            job.updated_at = notification.updated_at
            dao_update_job(job)


@notify_celery.task(name="process-job")
@statsd(namespace="tasks")
def process_job(job_id):
    start = datetime.utcnow()
    job = dao_get_job_by_id(job_id)

    if job.job_status != JOB_STATUS_PENDING:
        return

    service = job.service

    if not service.active:
        job.job_status = JOB_STATUS_CANCELLED
        dao_update_job(job)
        current_app.logger.warning("Job {} has been cancelled, service {} is inactive".format(job_id, service.id))
        return

    job.job_status = JOB_STATUS_IN_PROGRESS
    job.processing_started = start
    dao_update_job(job)

    # Record StatsD stats to compute SLOs
    job_start = job.scheduled_for or job.created_at
    statsd_client.timing_with_dates("job.processing-start-delay", job.processing_started, job_start)

    db_template = dao_get_template_by_id(job.template_id, job.template_version)

    TemplateClass = get_template_class(db_template.template_type)
    template = TemplateClass(db_template.__dict__)
    template.process_type = db_template.process_type

    current_app.logger.info("Starting job {} processing {} notifications".format(job_id, job.notification_count))

    csv = get_recipient_csv(job, template)

    rows = csv.get_rows()
    for result in chunked(rows, Config.BATCH_INSERTION_CHUNK_SIZE):
        process_rows(result, template, job, service)
        put_batch_saving_bulk_created(
            metrics_logger, 1, notification_type=db_template.template_type, priority=db_template.process_type
        )


def job_complete(job: Job, resumed=False, start=None):
    job.job_status = JOB_STATUS_FINISHED

    finished = datetime.utcnow()
    job.processing_finished = finished
    dao_update_job(job)

    if resumed:
        current_app.logger.info("Resumed Job {} completed at {}".format(job.id, job.created_at))
    else:
        current_app.logger.info(
            "Job {} created at {} started at {} finished at {}".format(job.id, job.created_at, start, finished)
        )


def process_rows(rows: List, template: Template, job: Job, service: Service):
    template_type = template.template_type
    sender_id = str(job.sender_id) if job.sender_id else None
    encrypted_smss: List[SignedNotification] = []
    encrypted_emails: List[SignedNotification] = []
    for row in rows:
        client_reference = row.get("reference", None)
        signed_row = SignedNotification(
            signer_notification.sign(
                {
                    "api_key": job.api_key_id and str(job.api_key_id),  # type: ignore
                    "key_type": job.api_key.key_type if job.api_key else KEY_TYPE_NORMAL,
                    "template": str(template.id),
                    "template_version": job.template_version,
                    "job": str(job.id),
                    "to": row.recipient,
                    "row_number": row.index,
                    "personalisation": dict(row.personalisation),
                    "queue": choose_sending_queue(str(template.process_type), template_type, job.notification_count),
                    "sender_id": sender_id,
                    "client_reference": client_reference.data,  # will return None if missing
                }
            )
        )
        if template_type == SMS_TYPE:
            encrypted_smss.append(signed_row)
        if template_type == EMAIL_TYPE:
            encrypted_emails.append(signed_row)

    # the same_sms and save_email task are going to be using template and service objects from cache
    # these objects are transient and will not have relationships loaded
    if encrypted_smss:
        save_smss.apply_async(
            (str(service.id), encrypted_smss, None),
            queue=choose_database_queue(str(template.process_type), service.research_mode, job.notification_count),
        )
    if encrypted_emails:
        save_emails.apply_async(
            (str(service.id), encrypted_emails, None),
            queue=choose_database_queue(str(template.process_type), service.research_mode, job.notification_count),
        )


def __sending_limits_for_job_exceeded(service, job: Job, job_id):
    error_message = None

    if job.template.template_type == SMS_TYPE:
        total_post_send = fetch_todays_requested_sms_count(service.id) + job.notification_count
        total_sent_this_fiscal = fetch_notification_status_totals_for_service_by_fiscal_year(
            service.id, get_fiscal_year(datetime.utcnow()), notification_type=SMS_TYPE
        )
        send_exceeds_annual_limit = (total_post_send + total_sent_this_fiscal) > service.sms_annual_limit
        send_exceeds_daily_limit = total_post_send > service.sms_daily_limit

        if send_exceeds_annual_limit and current_app.config["FF_ANNUAL_LIMIT"]:
            error_message = f"SMS annual limit of {service.sms_annual_limit} would be exceeded if job {job_id} is sent. Job size: {job.notification_count} Total SMS sent this fiscal + job size: {total_post_send + total_sent_this_fiscal} Over by: {total_post_send + total_sent_this_fiscal - service.sms_annual_limit}"
        elif send_exceeds_daily_limit:
            error_message = f"SMS daily limit of {service.sms_daily_limit} would be exceeded if job {job_id} is sent. Job size: {job.notification_count} Total SMS sent today + job size: {total_post_send} Over by: {total_post_send - service.sms_daily_limit}"
    else:
        total_post_send = fetch_todays_email_count(service.id) + job.notification_count
        total_sent_this_fiscal = fetch_notification_status_totals_for_service_by_fiscal_year(
            service.id, get_fiscal_year(datetime.utcnow()), notification_type=EMAIL_TYPE
        )
        send_exceeds_annual_limit = (total_post_send + total_sent_this_fiscal) > service.email_annual_limit
        send_exceeds_daily_limit = total_post_send > service.message_limit

        if send_exceeds_annual_limit and current_app.config["FF_ANNUAL_LIMIT"]:
            error_message = f"Email annual limit of {service.email_annual_limit} would be exceeded if job {job_id} is sent. Job size: {job.notification_count} Total email sent this fiscal + job size: {total_post_send + total_sent_this_fiscal} Over limit by: {total_post_send + total_sent_this_fiscal - service.email_annual_limit}"
        elif send_exceeds_daily_limit:
            error_message = f"Email daily limit of {service.email_annual_limit} would be exceeded if job {job_id} is sent. Job size: {job.notification_count} Total email sent today + job size: {total_post_send + total_sent_this_fiscal} Over limit by: {total_post_send + total_sent_this_fiscal - service.email_annual_limit}"

    if error_message:
        job.job_status = JOB_STATUS_SENDING_LIMITS_EXCEEDED
        job.processing_finished = datetime.utcnow()
        dao_update_job(job)
        current_app.logger.info(error_message)
        return True
    return False


@notify_celery.task(bind=True, name="save-smss", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def save_smss(self, service_id: Optional[str], signed_notifications: List[SignedNotification], receipt: Optional[UUID]):
    """
    Function that takes a list of signed notifications, stores
    them in the DB and then sends these to the queue. If the receipt
    is not None then it is passed to the RedisQueue to let it know it
    can delete the inflight notifications.
    """
    verified_notifications: List[VerifiedNotification] = []
    notification_id_queue: Dict = {}
    saved_notifications: List[Notification] = []
    for signed_notification in signed_notifications:
        try:
            _notification = signer_notification.verify(signed_notification)
        except BadSignature:
            current_app.logger.exception(f"Invalid signature for signed_notification {signed_notification}")
            raise
        service_id = _notification.get("service_id", service_id)  # take it it out of the notification if it's there
        service = dao_fetch_service_by_id(service_id, use_cache=True)

        template = dao_get_template_by_id(
            _notification.get("template"), version=_notification.get("template_version"), use_cache=True
        )
        # todo: _notification may not have "sender_id" key
        sender_id = _notification.get("sender_id")  # type: ignore
        notification_id = _notification.get("id", create_uuid())

        if "reply_to_text" in _notification and _notification["reply_to_text"]:
            reply_to_text = _notification["reply_to_text"]
        else:
            reply_to_text = ""  # type: ignore
            if sender_id:
                reply_to_text = try_validate_and_format_phone_number(
                    dao_get_service_sms_senders_by_id(service_id, sender_id).sms_sender
                )
            elif template.service:
                reply_to_text = template.get_reply_to_text()
            else:
                reply_to_text = service.get_default_sms_sender()  # type: ignore

        notification: VerifiedNotification = {
            **_notification,  # type: ignore
            "notification_id": notification_id,
            "reply_to_text": reply_to_text,
            "service": service,
            "key_type": _notification.get("key_type", KEY_TYPE_NORMAL),
            "template_id": template.id,
            "template_version": template.version,
            "recipient": _notification.get("to"),
            "personalisation": _notification.get("personalisation"),
            "notification_type": SMS_TYPE,  # type: ignore
            "simulated": _notification.get("simulated", None),
            "api_key_id": _notification.get("api_key", None),
            "created_at": datetime.utcnow(),
            "job_id": _notification.get("job", None),
            "job_row_number": _notification.get("row_number", None),
        }

        verified_notifications.append(notification)
        notification_id_queue[notification_id] = notification.get("queue")  # type: ignore
        process_type = template.process_type  # type: ignore

    try:
        # If the data is not present in the encrypted data then fallback on whats needed for process_job.
        saved_notifications = persist_notifications(verified_notifications)
        current_app.logger.debug(
            f"Saved following notifications into db: {notification_id_queue.keys()} associated with receipt {receipt}"
        )
        if receipt:
            acknowledge_receipt(SMS_TYPE, process_type, receipt)
            current_app.logger.debug(
                f"Batch saving: receipt_id {receipt} removed from buffer queue for notification_id {notification_id} for process_type {process_type}"
            )
        else:
            put_batch_saving_bulk_processed(
                metrics_logger,
                1,
                notification_type=SMS_TYPE,
                priority=process_type,  # type: ignore
            )

    except SQLAlchemyError as e:
        signed_and_verified = list(zip(signed_notifications, verified_notifications))
        handle_batch_error_and_forward(self, signed_and_verified, SMS_TYPE, e, receipt, template)

    current_app.logger.debug(f"Sending following sms notifications to AWS: {notification_id_queue.keys()}")
    for notification_obj in saved_notifications:
        try:
            queue = notification_id_queue.get(notification_obj.id) or get_delivery_queue_for_template(template)
            send_notification_to_queue(
                notification_obj,
                service.research_mode,
                queue=queue,
            )
            current_app.logger.debug(
                "SMS {} created at {} for job {}".format(
                    notification_obj.id,
                    notification_obj.created_at,
                    notification_obj.job,
                )
            )
        except (LiveServiceTooManySMSRequestsError, TrialServiceTooManySMSRequestsError) as e:
            current_app.logger.info(f"{e.message}: SMS {notification_obj.id} not created")


@notify_celery.task(bind=True, name="save-emails", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def save_emails(self, _service_id: Optional[str], signed_notifications: List[SignedNotification], receipt: Optional[UUID]):
    """
    Function that takes a list of signed notifications, stores
    them in the DB and then sends these to the queue. If the receipt
    is not None then it is passed to the RedisQueue to let it know it
    can delete the inflight notifications.
    """
    verified_notifications: List[VerifiedNotification] = []
    notification_id_queue: Dict = {}
    saved_notifications: List[Notification] = []

    # temporarily cache services so we don't get them more than once each batch
    service_id_map = {}

    for signed_notification in signed_notifications:
        try:
            _notification = signer_notification.verify(signed_notification)
        except BadSignature:
            current_app.logger.exception(f"Invalid signature for signed_notification {signed_notification}")
            raise
        service_id = _notification.get("service_id", _service_id)  # take it it out of the notification if it's there

        # get service from local cache if possible
        if service_id not in service_id_map:
            service = dao_fetch_service_by_id(service_id)
            service_id_map[service_id] = service
        else:
            service = service_id_map[service_id]

        template = dao_get_template_by_id(
            _notification.get("template"), version=_notification.get("template_version"), use_cache=True
        )
        # todo: _notification does not have key "sender_id"
        sender_id = _notification.get("sender_id")  # type: ignore
        notification_id = _notification.get("id", create_uuid())
        reply_to_text = ""  # type: ignore
        if (
            "reply_to_text" in _notification and _notification["reply_to_text"]
        ):  # first just see if we already have a value of this and use it, otherwise continue with the logic below
            reply_to_text = _notification["reply_to_text"]  # type: ignore
        else:
            if sender_id:
                reply_to_text = dao_get_reply_to_by_id(service_id, sender_id).email_address
            elif template.service:
                reply_to_text = template.get_reply_to_text()  # type: ignore
            else:
                reply_to_text = service.get_default_reply_to_email_address()

        notification: VerifiedNotification = {
            **_notification,  # type: ignore
            "notification_id": notification_id,
            "reply_to_text": reply_to_text,
            "service": service,
            "key_type": _notification.get("key_type", KEY_TYPE_NORMAL),
            "template_id": template.id,
            "template_version": template.version,
            "recipient": _notification.get("to"),
            "personalisation": _notification.get("personalisation"),
            "notification_type": EMAIL_TYPE,  # type: ignore
            "simulated": _notification.get("simulated", None),
            "api_key_id": _notification.get("api_key", None),
            "created_at": datetime.utcnow(),
            "job_id": _notification.get("job", None),
            "job_row_number": _notification.get("row_number", None),
        }

        verified_notifications.append(notification)
        notification_id_queue[notification_id] = notification.get("queue")  # type: ignore
        process_type = template.process_type

    try:
        # If the data is not present in the encrypted data then fallback on whats needed for process_job
        saved_notifications = persist_notifications(verified_notifications)
        current_app.logger.debug(
            f"Saved following notifications into db: {notification_id_queue.keys()} associated with receipt {receipt}"
        )
        if receipt:
            # todo: fix this potential bug
            # template is whatever it was set to last in the for loop above
            # at this point in the code we have a list of notifications (saved_notifications)
            # which could use multiple templates
            acknowledge_receipt(EMAIL_TYPE, process_type, receipt)
            current_app.logger.debug(
                f"Batch saving: receipt_id {receipt} removed from buffer queue for notification_id {notification_id} for process_type {process_type}"
            )
        else:
            put_batch_saving_bulk_processed(
                metrics_logger,
                1,
                notification_type=EMAIL_TYPE,
                priority=process_type,  # type: ignore
            )
    except SQLAlchemyError as e:
        signed_and_verified = list(zip(signed_notifications, verified_notifications))
        handle_batch_error_and_forward(self, signed_and_verified, EMAIL_TYPE, e, receipt, template)

    if saved_notifications:
        try_to_send_notifications_to_queue(notification_id_queue, service, saved_notifications, template)


def try_to_send_notifications_to_queue(notification_id_queue, service, saved_notifications, template):
    """
    Loop through saved_notifications, check if the service has hit their daily rate limit,
    and if not, call send_notification_to_queue on notification
    """
    current_app.logger.debug(f"Sending following email notifications to AWS: {notification_id_queue.keys()}")
    # todo: fix this potential bug
    # service is whatever it was set to last in the for loop above.
    # at this point in the code we have a list of notifications (saved_notifications)
    # which could be from multiple services
    research_mode = service.research_mode  # type: ignore
    for notification_obj in saved_notifications:
        try:
            queue = notification_id_queue.get(notification_obj.id) or get_delivery_queue_for_template(template)
            send_notification_to_queue(
                notification_obj,
                research_mode,
                queue,
            )

            current_app.logger.debug(
                "Email {} created at {} for job {}".format(
                    notification_obj.id,
                    notification_obj.created_at,
                    notification_obj.job,
                )
            )
        except (LiveServiceTooManyRequestsError, TrialServiceTooManyRequestsError) as e:
            current_app.logger.info(f"{e.message}: Email {notification_obj.id} not created")


def handle_batch_error_and_forward(
    task: Any,
    signed_and_verified: list[tuple[Any, Any]],
    notification_type: Optional[str],
    exception,
    receipt: Optional[UUID] = None,
    template: Any = None,
):
    if receipt:
        current_app.logger.warning(f"Batch saving: could not persist notifications with receipt {receipt}: {str(exception)}")
    else:
        current_app.logger.warning(f"Batch saving: could not persist notifications: {str(exception)}")
    process_type = template.process_type if template else None

    notifications_in_job: List[str] = []
    for signed, notification in signed_and_verified:
        notification_id = notification["notification_id"]
        notifications_in_job.append(notification_id)
        service = notification["service"]
        # Sometimes, SQS plays the same message twice. We should be able to catch an IntegrityError, but it seems
        # SQLAlchemy is throwing a FlushError. So we check if the notification id already exists then do not
        # send to the retry queue.
        found = get_notification_by_id(notification_id)
        if not found and service:
            forward_msg = "Batch saving: forwarding notification {notif} to individual save from receipt {receipt}.".format(
                notif=notification_id,
                receipt=receipt,
            )
            current_app.logger.info(forward_msg)
            save_fn = save_emails if notification_type == EMAIL_TYPE else save_smss

            template = dao_get_template_by_id(
                notification.get("template_id"), notification.get("template_version"), use_cache=True
            )
            process_type = template.process_type
            retry_msg = "{task} notification for job {job} row number {row} and notification id {notif} and max_retries are {max_retry}".format(
                task=task.__name__,
                job=notification.get("job", None),
                row=notification.get("row_number", None),
                notif=notification_id,
                max_retry=task.max_retries,
            )
            current_app.logger.warning("Retry " + retry_msg)
            try:
                # If >1 notification has failed, we want to make individual
                # tasks to retry those notifications.
                if len(signed_and_verified) > 1:
                    save_fn.apply_async(
                        (service.id, [signed], None),
                        queue=choose_database_queue(str(template.process_type), service.research_mode, notifications_count=1),
                    )
                    current_app.logger.warning("Made a new task to retry")
                else:
                    current_app.logger.warning("Retrying the current task")
                    task.retry(queue=QueueNames.RETRY, exc=exception)
            except task.MaxRetriesExceededError:
                current_app.logger.error("Max retry failed" + retry_msg)

    # end of the loop, purge the notifications from the buffer queue:
    if receipt:
        acknowledge_receipt(notification_type, process_type, receipt)
        current_app.logger.info(f"Acknowledged notification ids: {str(notifications_in_job)} for receipt: {str(receipt)}")


def get_template_class(template_type):
    if template_type == SMS_TYPE:
        return SMSMessageTemplate
    elif template_type in (EMAIL_TYPE, LETTER_TYPE):
        # since we don't need rendering capabilities (we only need to extract placeholders) both email and letter can
        # use the same base template
        return WithSubjectTemplate


def parse_dvla_file(filename):
    bucket_location = "{}-ftp".format(current_app.config["NOTIFY_EMAIL_DOMAIN"])
    response_file_content = s3.get_s3_file(bucket_location, filename)

    try:
        return process_updates_from_file(response_file_content)
    except TypeError:
        raise DVLAException("DVLA response file: {} has an invalid format".format(filename))


def get_billing_date_in_est_from_filename(filename):
    # exclude seconds from the date since we don't need it. We got a date ending in 60 second - which is not valid.
    datetime_string = filename.split("-")[1][:-2]
    datetime_obj = datetime.strptime(datetime_string, "%Y%m%d%H%M")
    return convert_utc_to_local_timezone(datetime_obj).date()


def process_updates_from_file(response_file):
    NotificationUpdate = namedtuple("NotificationUpdate", ["reference", "status", "page_count", "cost_threshold"])
    notification_updates = [NotificationUpdate(*line.split("|")) for line in response_file.splitlines()]
    return notification_updates


def check_billable_units(notification_update):
    notification = dao_get_notification_history_by_reference(notification_update.reference)

    if int(notification_update.page_count) != notification.billable_units:
        msg = "Notification with id {} has {} billable_units but DVLA says page count is {}".format(
            notification.id, notification.billable_units, notification_update.page_count
        )
        try:
            raise DVLAException(msg)
        except DVLAException:
            current_app.logger.exception(msg)


@notify_celery.task(bind=True, name="send-inbound-sms", max_retries=5, default_retry_delay=300)
@statsd(namespace="tasks")
def send_inbound_sms_to_service(self, inbound_sms_id, service_id):
    inbound_api = get_service_inbound_api_for_service(service_id=service_id)
    if not inbound_api:
        # No API data has been set for this service
        return

    inbound_sms = dao_get_inbound_sms_by_id(service_id=service_id, inbound_id=inbound_sms_id)
    data = {
        "id": str(inbound_sms.id),
        # TODO: should we be validating and formatting the phone number here?
        "source_number": inbound_sms.user_number,
        "destination_number": inbound_sms.notify_number,
        "message": inbound_sms.content,
        "date_received": inbound_sms.provider_date.strftime(DATETIME_FORMAT),
    }

    try:
        response = request(
            method="POST",
            url=inbound_api.url,
            data=json.dumps(data),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(inbound_api.bearer_token),
            },
            timeout=60,
        )
        current_app.logger.debug(
            "send_inbound_sms_to_service sending {} to {}, response {}".format(
                inbound_sms_id, inbound_api.url, response.status_code
            )
        )
        response.raise_for_status()
    except RequestException as e:
        current_app.logger.warning(
            "send_inbound_sms_to_service failed for service_id: {} for inbound_sms_id: {} and url: {}. exc: {}".format(
                service_id, inbound_sms_id, inbound_api.url, e
            )
        )
        if not isinstance(e, HTTPError) or e.response.status_code >= 500:
            try:
                self.retry(queue=QueueNames.RETRY)
            except self.MaxRetriesExceededError:
                current_app.logger.error(
                    """Retry: send_inbound_sms_to_service has retried the max number of
                     times for service: {} and  inbound_sms {}""".format(service_id, inbound_sms_id)
                )


@notify_celery.task(name="process-incomplete-jobs")
@statsd(namespace="tasks")
def process_incomplete_jobs(job_ids):
    jobs = [dao_get_job_by_id(job_id) for job_id in job_ids]

    # reset the processing start time so that the check_job_status scheduled task doesn't pick this job up again
    for job in jobs:
        job.processing_started = datetime.utcnow()
        dao_update_job(job)

    current_app.logger.info("Resuming Job(s) {}".format(job_ids))
    for job_id in job_ids:
        process_incomplete_job(job_id)


def process_incomplete_job(job_id):
    job = dao_get_job_by_id(job_id)

    last_notification_added = dao_get_last_notification_added_for_job_id(job_id)

    if last_notification_added:
        resume_from_row = last_notification_added.job_row_number + 1
    else:
        resume_from_row = 0  # no rows have been added yet, resume from row 0

    current_app.logger.info("Resuming job {} from row {}".format(job_id, resume_from_row))

    db_template = dao_get_template_by_id(job.template_id, job.template_version)

    TemplateClass = get_template_class(db_template.template_type)
    template = TemplateClass(db_template.__dict__)
    template.process_type = db_template.process_type

    csv = get_recipient_csv(job, template)
    rows = csv.get_rows()  # This returns an iterator
    for result in chunked(islice(rows, resume_from_row, None), Config.BATCH_INSERTION_CHUNK_SIZE):
        process_rows(result, template, job, job.service)
        put_batch_saving_bulk_created(
            metrics_logger, 1, notification_type=db_template.template_type, priority=db_template.process_type
        )


def choose_database_queue(process_type: str, research_mode: bool, notifications_count: int) -> str:
    # Research mode is a special case, it always goes to the research mode queue.
    if research_mode:
        return QueueNames.RESEARCH_MODE

    # We redirect first to a queue depending on its notification' size.
    large_csv_threshold = current_app.config["CSV_BULK_REDIRECT_THRESHOLD"]
    if notifications_count >= large_csv_threshold:
        return QueueNames.BULK_DATABASE
    # Don't switch to normal queue if it's already set to priority queue.
    elif process_type == BULK:
        return QueueNames.NORMAL_DATABASE
    else:
        # If the size isn't a concern, fall back to the template's process type.
        if process_type == PRIORITY:
            return QueueNames.PRIORITY_DATABASE
        elif process_type == BULK:
            return QueueNames.BULK_DATABASE
        else:
            return QueueNames.NORMAL_DATABASE


def choose_sending_queue(process_type: str, notif_type: str, notifications_count: int) -> Optional[str]:
    """Determine which queue to use depending on given parameters.

    We only check one rule at the moment: if the CSV file is big enough,
    the notifications will be sent to the bulk queue so they don't slow down
    notifications that are transactional in nature.
    """
    large_csv_threshold = current_app.config["CSV_BULK_REDIRECT_THRESHOLD"]
    # Default to the pre-configured template's process type.
    queue: Optional[str] = process_type

    if notifications_count >= large_csv_threshold:
        queue = QueueNames.DELIVERY_QUEUES[notif_type][Priorities.LOW]
    # If priority is slow/bulk, but lower than threshold, let's make it
    # faster by switching to normal queue.
    elif process_type == BULK:
        queue = QueueNames.DELIVERY_QUEUES[notif_type][Priorities.MEDIUM]
    else:
        # If the size isn't a concern, fall back to the template's process type.
        queue = QueueNames.DELIVERY_QUEUES[notif_type][Priorities.to_lmh(process_type)]
    return queue


@notify_celery.task(bind=True, name="send-notify-no-reply", max_retries=5)
@statsd(namespace="tasks")
def send_notify_no_reply(self, data):
    """Sends no-reply emails to people replying back to GCNotify.

    This task will be fed by the AWS lambda code ses_receiving_emails.
    https://github.com/cds-snc/notification-lambdas/blob/fd508d9718cef715f9297fedd8d780bc4bae0051/sesreceivingemails/ses_receiving_emails.py
    """
    payload = json.loads(data)

    service = dao_fetch_service_by_id(current_app.config["NOTIFY_SERVICE_ID"])
    template = dao_get_template_by_id(current_app.config["NO_REPLY_TEMPLATE_ID"])

    try:
        data_to_send = [
            dict(
                template_id=template.id,
                template_version=template.version,
                recipient=payload["sender"],
                service=service,
                personalisation={"sending_email_address": payload["recipients"][0]},
                notification_type=template.template_type,
                api_key_id=None,
                key_type=KEY_TYPE_NORMAL,
                # Ensure that the reply to is not set, if people reply
                # to these emails, they will go to the GC Notify service
                # email address, and we handle those on the SES inbound
                # Lambda
                reply_to_text=None,
            )
        ]
        saved_notifications = persist_notifications(data_to_send)
        send_notification_to_queue(saved_notifications[0], False, queue=QueueNames.NOTIFY)
    except Exception as e:
        try:
            current_app.logger.warning(f"The exception is {repr(e)}")
            self.retry(queue=QueueNames.RETRY)
        except self.MaxRetriesExceededError:
            current_app.logger.error(
                f"""
                Retry: send_notify_no_reply has retried the max number of
                 times for sender {payload['sender']}"""
            )


def get_recipient_csv(job: Job, template: Template) -> RecipientCSV:
    return RecipientCSV(
        s3.get_job_from_s3(str(job.service_id), str(job.id)),
        template_type=template.template_type,
        placeholders=template.placeholders,
        max_rows=get_csv_max_rows(job.service_id),
    )


def acknowledge_receipt(notification_type: Any, process_type: Any, receipt: UUID):  # noqa
    """
    Acknowledge the notification has been saved to the DB and sent to the service.

    Args:
    notification_type: str
        Type of notification being sent; either SMS_TYPE or EMAIL_TYPE
    template: model.Template
        Template used to send notification

    Returns: None
    """
    queue_for = {
        (SMS_TYPE, PRIORITY): sms_priority,
        (SMS_TYPE, NORMAL): sms_normal,
        (SMS_TYPE, BULK): sms_bulk,
        (EMAIL_TYPE, PRIORITY): email_priority,
        (EMAIL_TYPE, NORMAL): email_normal,
        (EMAIL_TYPE, BULK): email_bulk,
    }
    queue = queue_for.get((notification_type, process_type))
    if queue is None:
        raise ValueError(
            f"acknowledge_receipt: No queue found for receipt {receipt} notification type {notification_type} and process type {process_type}"
        )
    if queue.acknowledge(receipt):
        return

    current_app.logger.warning(f"acknowledge_receipt: trying to acknowledge inflight everywhere for receipt {receipt}")
    if (
        sms_priority.acknowledge(receipt)
        or sms_normal.acknowledge(receipt)
        or sms_bulk.acknowledge(receipt)
        or email_priority.acknowledge(receipt)
        or email_normal.acknowledge(receipt)
        or email_bulk.acknowledge(receipt)
    ):
        return
    else:
        current_app.logger.warning(f"acknowledge_receipt: receipt {receipt} not found in any queue")


@notify_celery.task(name="seed-bounce-rate-in-redis")
@statsd(namespace="tasks")
def seed_bounce_rate_in_redis(service_id: str, interval: int = 24):
    """
    Function to seed both the total_notifications and total_hard_bounces in Redis for a given service
    over a given interval (default 24 hours)

    Args:
        service_id (str): The service id to seed bounce rate for
        interval: The number of hours to seed bounce rate for
    """
    if bounce_rate_client.get_seeding_started(service_id) is False:
        current_app.logger.info("Clear all data for current service {}".format(service_id))
        bounce_rate_client.clear_bounce_rate_data(service_id)
        current_app.logger.info("Set seeding flag to True for service {}".format(service_id))
        bounce_rate_client.set_seeding_started(service_id)
    else:
        current_app.logger.info("Bounce rate already seeded for service_id {}".format(service_id))
        return

    current_app.logger.info("Seeding bounce rate for service_id {}".format(service_id))
    total_seeded_notifications = total_notifications_grouped_by_hour(service_id, interval=interval)
    total_seeded_hard_bounces = total_hard_bounces_grouped_by_hour(service_id, interval=interval)

    for hour, total_notifications in total_seeded_notifications:
        # set the timestamp to the start of the hour + 1 second to ensure the notification
        # will be counted in the correct hour
        hour_timestamp_s = int(hour.timestamp()) + 1
        # generate a list of tuples of (UUID, timestamp) that will be used to seed Redis
        email_data = [(str(uuid4()), hour_timestamp_s) for _ in range(total_notifications)]
        email_data_dict = dict(email_data)
        bounce_rate_client.set_notifications_seeded(service_id, email_data_dict)
    current_app.logger.info(f"Seeded total notification data for service {service_id} in Redis")

    for hour, total_hard_bounces in total_seeded_hard_bounces:
        # set the timestamp to the start of the hour + 1 second to ensure the notification
        # will be counted in the correct hour
        hour_timestamp_s = int(hour.timestamp()) + 1
        # generate a list of tuples of (UUID, timestamp) that will be used to seed Redis
        bounce_data = [(str(uuid4()), hour_timestamp_s) for _ in range(total_hard_bounces)]
        bounce_data_dict = dict(bounce_data)
        bounce_rate_client.set_hard_bounce_seeded(service_id, bounce_data_dict)

    current_app.logger.info(f"Seeded hard bounce data for service {service_id} in Redis")
