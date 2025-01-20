import json
import os
from datetime import timedelta
from typing import Any, List

from dotenv import load_dotenv
from environs import Env
from fido2.server import Fido2Server
from fido2.webauthn import PublicKeyCredentialRpEntity
from kombu import Exchange, Queue
from notifications_utils import logging

from celery.schedules import crontab

env = Env()
env.read_env()
load_dotenv()


class Priorities(object):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BULK = "bulk"
    NORMAL = "normal"
    PRIORITY = "priority"

    @staticmethod
    def to_lmh(priority: str) -> str:
        """
        Convert bulk / normal / priority to low / medium / high. Anything else left alone.

        Args:
            priority (str): priority to convert.

        Returns:
            str: low, medium, or high
        """

        if priority == Priorities.BULK:
            return Priorities.LOW
        elif priority == Priorities.NORMAL:
            return Priorities.MEDIUM
        elif priority == Priorities.PRIORITY:
            return Priorities.HIGH
        else:
            return priority


class QueueNames(object):
    # Periodic tasks executed by Notify.
    PERIODIC = "periodic-tasks"

    # For high priority tasks. The queue should be kept at relatively low volume
    # and fast processing.
    PRIORITY = "priority-tasks"

    # For bulk send of notifications. This can be high volume and flushed over time.
    # It would get most traffic coming from the API for example.
    BULK = "bulk-tasks"

    NORMAL = "normal-tasks"

    # A queue meant for database tasks but it seems to be the default for sending
    # notifications in some occasion. Need to investigate the purpose of this one
    # further.
    DATABASE = "database-tasks"

    # database operations for high priority notifications
    PRIORITY_DATABASE = "-priority-database-tasks.fifo"

    # database operations for normal priority notifications
    NORMAL_DATABASE = "-normal-database-tasks"

    # database operations for bulk notifications
    BULK_DATABASE = "-bulk-database-tasks"

    # A queue for the tasks associated with the batch saving
    NOTIFY_CACHE = "notifiy-cache-tasks"

    # Queues for sending all SMS, except long dedicated numbers.
    SEND_SMS_HIGH = "send-sms-high"
    SEND_SMS_MEDIUM = "send-sms-medium"
    SEND_SMS_LOW = "send-sms-low"

    # Primarily used for long dedicated numbers sent from us-west-2 upon which
    # we have a limit to send per second and hence, needs to be throttled.
    SEND_THROTTLED_SMS = "send-throttled-sms-tasks"

    # Queues for sending all emails.
    SEND_EMAIL_HIGH = "send-email-high"
    SEND_EMAIL_MEDIUM = "send-email-medium"
    SEND_EMAIL_LOW = "send-email-low"

    # The research mode queue for notifications that are tested by users trying
    # out Notify.
    RESEARCH_MODE = "research-mode-tasks"
    REPORTING = "reporting-tasks"

    # Queue for scheduled notifications.
    JOBS = "job-tasks"

    # Queue for tasks to retry.
    RETRY = "retry-tasks"

    NOTIFY = "notify-internal-tasks"
    PROCESS_FTP = "process-ftp-tasks"
    CREATE_LETTERS_PDF = "create-letters-pdf-tasks"
    CALLBACKS = "service-callbacks"
    CALLBACKS_RETRY = "service-callbacks-retry"

    # Queue for letters, unused by CDS at this time as we don't use these.
    LETTERS = "letter-tasks"

    # Queue for antivirus/malware tasks
    ANTIVIRUS = "antivirus-tasks"

    # Queue for delivery receipts such as emails sent through AWS SES.
    DELIVERY_RECEIPTS = "delivery-receipts"

    DELIVERY_QUEUES = {
        "sms": {
            Priorities.LOW: SEND_SMS_LOW,
            Priorities.MEDIUM: SEND_SMS_MEDIUM,
            Priorities.HIGH: SEND_SMS_HIGH,
        },
        "email": {
            Priorities.LOW: SEND_EMAIL_LOW,
            Priorities.MEDIUM: SEND_EMAIL_MEDIUM,
            Priorities.HIGH: SEND_EMAIL_HIGH,
        },
        "letter": {
            Priorities.LOW: BULK,
            Priorities.MEDIUM: NORMAL,
            Priorities.HIGH: PRIORITY,
        },
    }

    @staticmethod
    def all_queues():
        return [
            QueueNames.PRIORITY,
            QueueNames.PERIODIC,
            QueueNames.BULK,
            QueueNames.DATABASE,
            QueueNames.PRIORITY_DATABASE,
            QueueNames.NORMAL_DATABASE,
            QueueNames.BULK_DATABASE,
            QueueNames.SEND_SMS_HIGH,
            QueueNames.SEND_SMS_MEDIUM,
            QueueNames.SEND_SMS_LOW,
            QueueNames.SEND_THROTTLED_SMS,
            QueueNames.SEND_EMAIL_HIGH,
            QueueNames.SEND_EMAIL_MEDIUM,
            QueueNames.SEND_EMAIL_LOW,
            QueueNames.RESEARCH_MODE,
            QueueNames.REPORTING,
            QueueNames.JOBS,
            QueueNames.RETRY,
            QueueNames.CALLBACKS_RETRY,
            QueueNames.NOTIFY,
            # QueueNames.CREATE_LETTERS_PDF,
            QueueNames.CALLBACKS,
            # QueueNames.LETTERS,
            QueueNames.DELIVERY_RECEIPTS,
        ]


class TaskNames(object):
    PROCESS_INCOMPLETE_JOBS = "process-incomplete-jobs"
    ZIP_AND_SEND_LETTER_PDFS = "zip-and-send-letter-pdfs"
    SCAN_FILE = "scan-file"


class Config(object):
    # URL of admin app
    ADMIN_BASE_URL = os.getenv("ADMIN_BASE_URL", "http://localhost:6012")

    # URL of api app (on AWS this is the internal api endpoint)
    API_HOST_NAME = os.getenv("API_HOST_NAME")

    # admin app api key
    ADMIN_CLIENT_SECRET = os.getenv("ADMIN_CLIENT_SECRET")

    # encyption secret/salt
    SECRET_KEY = env.list("SECRET_KEY", [])
    DANGEROUS_SALT = os.getenv("DANGEROUS_SALT")

    # API key prefix
    API_KEY_PREFIX = "gcntfy-"

    # DB conection string
    SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
    SQLALCHEMY_DATABASE_READER_URI = os.getenv("SQLALCHEMY_DATABASE_READER_URI")
    # By making the database reader optional, we can revert to a single writer
    # instance configuration easily.
    if SQLALCHEMY_DATABASE_READER_URI:
        SQLALCHEMY_BINDS = {
            "writer": SQLALCHEMY_DATABASE_URI,
            "reader": SQLALCHEMY_DATABASE_READER_URI,
        }

    # Hosted graphite statsd prefix
    STATSD_PREFIX = os.getenv("STATSD_PREFIX")

    # Prefix to identify queues in SQS
    NOTIFICATION_QUEUE_PREFIX = os.getenv("NOTIFICATION_QUEUE_PREFIX")

    # URL of redis instance
    REDIS_URL = os.getenv("REDIS_URL")
    REDIS_PUBLISH_URL = os.getenv("REDIS_PUBLISH_URL", REDIS_URL)
    REDIS_ENABLED = env.bool("REDIS_ENABLED", False)
    EXPIRE_CACHE_TEN_MINUTES = 600
    EXPIRE_CACHE_EIGHT_DAYS = 8 * 24 * 60 * 60

    # Performance platform
    PERFORMANCE_PLATFORM_ENABLED = False
    PERFORMANCE_PLATFORM_URL = "https://www.performance.service.gov.uk/data/govuk-notify/"

    # Freshdesk
    FRESH_DESK_PRODUCT_ID = os.getenv("FRESH_DESK_PRODUCT_ID")
    FRESH_DESK_API_URL = os.getenv("FRESH_DESK_API_URL")
    FRESH_DESK_API_KEY = os.getenv("FRESH_DESK_API_KEY")
    FRESH_DESK_ENABLED = env.bool("FRESH_DESK_ENABLED", True)

    # Salesforce
    SALESFORCE_DOMAIN = os.getenv("SALESFORCE_DOMAIN")
    SALESFORCE_CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID", "Notify")
    SALESFORCE_ENGAGEMENT_PRODUCT_ID = os.getenv("SALESFORCE_ENGAGEMENT_PRODUCT_ID")
    SALESFORCE_ENGAGEMENT_RECORD_TYPE = os.getenv("SALESFORCE_ENGAGEMENT_RECORD_TYPE")
    SALESFORCE_ENGAGEMENT_STANDARD_PRICEBOOK_ID = os.getenv("SALESFORCE_ENGAGEMENT_STANDARD_PRICEBOOK_ID")
    SALESFORCE_GENERIC_ACCOUNT_ID = os.getenv("SALESFORCE_GENERIC_ACCOUNT_ID")
    SALESFORCE_USERNAME = os.getenv("SALESFORCE_USERNAME")
    SALESFORCE_PASSWORD = os.getenv("SALESFORCE_PASSWORD")
    SALESFORCE_SECURITY_TOKEN = os.getenv("SALESFORCE_SECURITY_TOKEN")
    CRM_GITHUB_PERSONAL_ACCESS_TOKEN = os.getenv("CRM_GITHUB_PERSONAL_ACCESS_TOKEN")
    CRM_ORG_LIST_URL = os.getenv("CRM_ORG_LIST_URL")

    # Logging
    DEBUG = False
    NOTIFY_LOG_PATH = os.getenv("NOTIFY_LOG_PATH")

    # Xray SDK
    AWS_XRAY_ENABLED = env.bool("AWS_XRAY_SDK_ENABLED", False)  # X-Ray switch leveraged by the SDK

    # Cronitor
    CRONITOR_ENABLED = False
    CRONITOR_KEYS = json.loads(os.getenv("CRONITOR_KEYS", "{}"))

    # PII check
    SCAN_FOR_PII = env.bool("SCAN_FOR_PII", False)

    # Documentation
    DOCUMENTATION_DOMAIN = os.getenv("DOCUMENTATION_DOMAIN", "documentation.notification.canada.ca")

    ###########################
    # Default config values ###
    ###########################

    NOTIFY_ENVIRONMENT = os.getenv("NOTIFY_ENVIRONMENT", "development")
    ADMIN_CLIENT_USER_NAME = "notify-admin"
    ATTACHMENT_NUM_LIMIT = env.int("ATTACHMENT_NUM_LIMIT", 10)  # Limit of 10 attachments per notification.
    ATTACHMENT_SIZE_LIMIT = env.int(
        "ATTACHMENT_SIZE_LIMIT", 1024 * 1024 * 10
    )  # 10 megabytes limit by default per single attachment
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    AWS_ROUTE53_ZONE = os.getenv("AWS_ROUTE53_ZONE", "Z2OW036USASMAK")
    AWS_SES_REGION = os.getenv("AWS_SES_REGION", "us-east-1")
    AWS_SES_ACCESS_KEY = os.getenv("AWS_SES_ACCESS_KEY")
    AWS_SES_SECRET_KEY = os.getenv("AWS_SES_SECRET_KEY")
    AWS_PINPOINT_REGION = os.getenv("AWS_PINPOINT_REGION", "us-west-2")
    AWS_PINPOINT_SC_POOL_ID = os.getenv("AWS_PINPOINT_SC_POOL_ID", "")
    AWS_PINPOINT_DEFAULT_POOL_ID = os.getenv("AWS_PINPOINT_DEFAULT_POOL_ID", "")
    AWS_PINPOINT_CONFIGURATION_SET_NAME = os.getenv("AWS_PINPOINT_CONFIGURATION_SET_NAME", "pinpoint-configuration")
    AWS_PINPOINT_SC_TEMPLATE_IDS = env.list("AWS_PINPOINT_SC_TEMPLATE_IDS", [])
    AWS_US_TOLL_FREE_NUMBER = os.getenv("AWS_US_TOLL_FREE_NUMBER")
    CSV_UPLOAD_BUCKET_NAME = os.getenv("CSV_UPLOAD_BUCKET_NAME", "notification-alpha-canada-ca-csv-upload")
    ASSET_DOMAIN = os.getenv("ASSET_DOMAIN", "assets.notification.canada.ca")
    INVITATION_EXPIRATION_DAYS = 2
    NOTIFY_APP_NAME = "api"
    SQLALCHEMY_RECORD_QUERIES = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_SIZE = env.int("SQLALCHEMY_POOL_SIZE", 5)
    SQLALCHEMY_POOL_TIMEOUT = 30
    SQLALCHEMY_POOL_RECYCLE = 300
    SQLALCHEMY_ECHO = env.bool("SQLALCHEMY_ECHO", False)
    PAGE_SIZE = 50
    PERSONALISATION_SIZE_LIMIT = env.int(
        "PERSONALISATION_SIZE_LIMIT", 1024 * 50
    )  # 50k bytes limit by default for personalisation data per notification
    API_PAGE_SIZE = 250
    TEST_MESSAGE_FILENAME = "Test message"
    ONE_OFF_MESSAGE_FILENAME = "Report"
    MAX_VERIFY_CODE_COUNT = 10
    JOBS_MAX_SCHEDULE_HOURS_AHEAD = 96
    FAILED_LOGIN_LIMIT = os.getenv("FAILED_LOGIN_LIMIT", 10)

    # be careful increasing this size without being sure that we won't see slowness in pysftp
    MAX_LETTER_PDF_ZIP_FILESIZE = 40 * 1024 * 1024  # 40mb
    MAX_LETTER_PDF_COUNT_PER_ZIP = 500

    CHECK_PROXY_HEADER = False

    # Notify's notifications templates
    NOTIFY_SERVICE_ID = "d6aa2c68-a2d9-4437-ab19-3ae8eb202553"
    NOTIFY_USER_ID = "6af522d0-2915-4e52-83a3-3690455a5fe6"
    INVITATION_EMAIL_TEMPLATE_ID = "4f46df42-f795-4cc4-83bb-65ca312f49cc"
    SMS_CODE_TEMPLATE_ID = "36fb0730-6259-4da1-8a80-c8de22ad4246"
    EMAIL_2FA_TEMPLATE_ID = "299726d2-dba6-42b8-8209-30e1d66ea164"
    EMAIL_MAGIC_LINK_TEMPLATE_ID = "6e97fd09-6da0-4cc8-829d-33cf5b818103"
    NEW_USER_EMAIL_VERIFICATION_TEMPLATE_ID = "ece42649-22a8-4d06-b87f-d52d5d3f0a27"
    PASSWORD_RESET_TEMPLATE_ID = "474e9242-823b-4f99-813d-ed392e7f1201"
    FORCED_PASSWORD_RESET_TEMPLATE_ID = "e9a65a6b-497b-42f2-8f43-1736e43e13b3"
    ALREADY_REGISTERED_EMAIL_TEMPLATE_ID = "0880fbb1-a0c6-46f0-9a8e-36c986381ceb"
    CHANGE_EMAIL_CONFIRMATION_TEMPLATE_ID = "eb4d9930-87ab-4aef-9bce-786762687884"
    SERVICE_NOW_LIVE_TEMPLATE_ID = "618185c6-3636-49cd-b7d2-6f6f5eb3bdde"
    ORGANISATION_INVITATION_EMAIL_TEMPLATE_ID = "203566f0-d835-47c5-aa06-932439c86573"
    TEAM_MEMBER_EDIT_EMAIL_TEMPLATE_ID = "c73f1d71-4049-46d5-a647-d013bdeca3f0"
    TEAM_MEMBER_EDIT_MOBILE_TEMPLATE_ID = "8a31520f-4751-4789-8ea1-fe54496725eb"
    REPLY_TO_EMAIL_ADDRESS_VERIFICATION_TEMPLATE_ID = "a42f1d17-9404-46d5-a647-d013bdfca3e1"
    MOU_SIGNER_RECEIPT_TEMPLATE_ID = "4fd2e43c-309b-4e50-8fb8-1955852d9d71"
    MOU_SIGNED_ON_BEHALF_SIGNER_RECEIPT_TEMPLATE_ID = "c20206d5-bf03-4002-9a90-37d5032d9e84"
    MOU_SIGNED_ON_BEHALF_ON_BEHALF_RECEIPT_TEMPLATE_ID = "522b6657-5ca5-4368-a294-6b527703bd0b"
    MOU_NOTIFY_TEAM_ALERT_TEMPLATE_ID = "d0e66c4c-0c50-43f0-94f5-f85b613202d4"
    CONTACT_US_TEMPLATE_ID = "8ea9b7a0-a824-4dd3-a4c3-1f508ed20a69"
    ACCOUNT_CHANGE_TEMPLATE_ID = "5b39e16a-9ff8-487c-9bfb-9e06bdb70f36"
    BRANDING_REQUEST_TEMPLATE_ID = "7d423d9e-e94e-4118-879d-d52f383206ae"
    NO_REPLY_TEMPLATE_ID = "86950840-6da4-4865-841b-16028110e980"
    NEAR_DAILY_LIMIT_TEMPLATE_ID = "5d3e4322-4ee6-457a-a710-c48755f6b643"
    REACHED_DAILY_LIMIT_TEMPLATE_ID = "fd29f796-fcdc-471b-a0d4-0093880d9173"
    DAILY_LIMIT_UPDATED_TEMPLATE_ID = "b3c766e6-be32-4edf-b8db-0f04ef404edc"
    NEAR_DAILY_SMS_LIMIT_TEMPLATE_ID = "a796568f-a89b-468e-b635-8105554301b9"
    REACHED_DAILY_SMS_LIMIT_TEMPLATE_ID = "a646e614-c527-4f94-a955-ed7185d577f4"
    DAILY_SMS_LIMIT_UPDATED_TEMPLATE_ID = "6ec12dd0-680a-4073-8d58-91d17cc8442f"
    CONTACT_FORM_DIRECT_EMAIL_TEMPLATE_ID = "b04beb4a-8408-4280-9a5c-6a046b6f7704"
    CONTACT_FORM_SENSITIVE_SERVICE_EMAIL_TEMPLATE_ID = "4bf8c15b-7393-463f-b6fe-e3fd1e99a03d"
    NEAR_DAILY_EMAIL_LIMIT_TEMPLATE_ID = "9aa60ad7-2d7f-46f0-8cbe-2bac3d4d77d8"
    REACHED_DAILY_EMAIL_LIMIT_TEMPLATE_ID = "ee036547-e51b-49f1-862b-10ea982cfceb"
    DAILY_EMAIL_LIMIT_UPDATED_TEMPLATE_ID = "97dade64-ea8d-460f-8a34-900b74ee5eb0"

    # Templates for annual limits
    REACHED_ANNUAL_LIMIT_TEMPLATE_ID = "ca6d9205-d923-4198-acdd-d0aa37725c37"
    ANNUAL_LIMIT_UPDATED_TEMPLATE_ID = "8381fdc3-95ad-4219-b07c-93aa808b67fa"
    NEAR_ANNUAL_LIMIT_TEMPLATE_ID = "1a7a1f01-7fd0-43e5-93a4-982e25a78816"
    ANNUAL_LIMIT_QUARTERLY_USAGE_TEMPLATE_ID = "f66a1025-17f5-471c-a7ab-37d6b9e9d304"

    APIKEY_REVOKE_TEMPLATE_ID = "a0a4e7b8-8a6a-4eaa-9f4e-9c3a5b2dbcf3"
    HEARTBEAT_TEMPLATE_EMAIL_LOW = "73079cb9-c169-44ea-8cf4-8d397711cc9d"
    HEARTBEAT_TEMPLATE_EMAIL_MEDIUM = "c75c4539-3014-4c4c-96b5-94d326758a74"
    HEARTBEAT_TEMPLATE_EMAIL_HIGH = "276da251-3103-49f3-9054-cbf6b5d74411"
    HEARTBEAT_TEMPLATE_SMS_LOW = "ab3a603b-d602-46ea-8c83-e05cb280b950"
    HEARTBEAT_TEMPLATE_SMS_MEDIUM = "a48b54ce-40f6-4e4a-abe8-1e2fa389455b"
    HEARTBEAT_TEMPLATE_SMS_HIGH = "4969a9e9-ddfd-476e-8b93-6231e6f1be4a"
    DEFAULT_TEMPLATE_CATEGORY_LOW = "0dda24c2-982a-4f44-9749-0e38b2607e89"
    DEFAULT_TEMPLATE_CATEGORY_MEDIUM = "f75d6706-21b7-437e-b93a-2c0ab771e28e"
    DEFAULT_TEMPLATE_CATEGORY_HIGH = "c4f87d7c-a55b-4c0f-91fe-e56c65bb1871"

    # UUIDs for Cypress tests
    CYPRESS_SERVICE_ID = "d4e8a7f4-2b8a-4c9a-8b3f-9c2d4e8a7f4b"
    CYPRESS_TEST_USER_ID = "e5f9d8c7-3a9b-4d8c-9b4f-8d3e5f9d8c7a"
    CYPRESS_TEST_USER_ADMIN_ID = "4f8b8b1e-9c4f-4d8b-8b1e-4f8b8b1e9c4f"
    CYPRESS_SMOKE_TEST_EMAIL_TEMPLATE_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    CYPRESS_SMOKE_TEST_SMS_TEMPLATE_ID = "e4b8f8d0-6a3b-4b9e-8c2b-1f2d3e4a5b6c"
    CYPRESS_USER_PW_SECRET = os.getenv("CYPRESS_USER_PW_SECRET")

    # Allowed service IDs able to send HTML through their templates.
    ALLOW_HTML_SERVICE_IDS: List[str] = [id.strip() for id in os.getenv("ALLOW_HTML_SERVICE_IDS", "").split(",")]

    BATCH_INSERTION_CHUNK_SIZE = int(os.getenv("BATCH_INSERTION_CHUNK_SIZE", 500))

    BROKER_URL = "sqs://"
    BROKER_TRANSPORT_OPTIONS = {
        "region": AWS_REGION,
        "polling_interval": 1,  # 1 second
        "visibility_timeout": 310,
        "queue_name_prefix": NOTIFICATION_QUEUE_PREFIX,
    }
    CELERY_ENABLE_UTC = True
    CELERY_TIMEZONE = os.getenv("TIMEZONE", "UTC")
    CELERY_ACCEPT_CONTENT = ["json"]
    CELERY_TASK_SERIALIZER = "json"
    CELERY_IMPORTS = (
        "app.celery.tasks",
        "app.celery.scheduled_tasks",
        "app.celery.reporting_tasks",
        "app.celery.nightly_tasks",
        "app.celery.process_pinpoint_receipts_tasks",
    )
    CELERYBEAT_SCHEDULE = {
        # app/celery/scheduled_tasks.py
        "run-scheduled-jobs": {
            "task": "run-scheduled-jobs",
            "schedule": crontab(),
            "options": {"queue": QueueNames.PERIODIC},
        },
        "delete-verify-codes": {
            "task": "delete-verify-codes",
            "schedule": timedelta(minutes=63),
            "options": {"queue": QueueNames.PERIODIC},
        },
        "delete-invitations": {
            "task": "delete-invitations",
            "schedule": timedelta(minutes=66),
            "options": {"queue": QueueNames.PERIODIC},
        },
        "mark-jobs-complete": {
            "task": "mark-jobs-complete",
            "schedule": crontab(),
            "options": {"queue": QueueNames.PERIODIC},
        },
        "check-job-status": {
            "task": "check-job-status",
            "schedule": crontab(),
            "options": {"queue": QueueNames.PERIODIC},
        },
        "replay-created-notifications": {
            "task": "replay-created-notifications",
            "schedule": crontab(minute="0, 15, 30, 45"),
            "options": {"queue": QueueNames.PERIODIC},
        },
        "in-flight-to-inbox": {
            "task": "in-flight-to-inbox",
            "schedule": 60,
            "options": {"queue": QueueNames.PERIODIC},
        },
        "beat-inbox-sms-normal": {
            "task": "beat-inbox-sms-normal",
            "schedule": 10,
            "options": {"queue": QueueNames.PERIODIC},
        },
        "beat-inbox-sms-bulk": {
            "task": "beat-inbox-sms-bulk",
            "schedule": 10,
            "options": {"queue": QueueNames.PERIODIC},
        },
        "beat-inbox-sms-priority": {
            "task": "beat-inbox-sms-priority",
            "schedule": 10,
            "options": {"queue": QueueNames.PERIODIC},
        },
        "beat-inbox-email-normal": {
            "task": "beat-inbox-email-normal",
            "schedule": 10,
            "options": {"queue": QueueNames.PERIODIC},
        },
        "beat-inbox-email-bulk": {
            "task": "beat-inbox-email-bulk",
            "schedule": 10,
            "options": {"queue": QueueNames.PERIODIC},
        },
        "beat-inbox-email-priority": {
            "task": "beat-inbox-email-priority",
            "schedule": 10,
            "options": {"queue": QueueNames.PERIODIC},
        },
        # app/celery/nightly_tasks.py
        "timeout-sending-notifications": {
            "task": "timeout-sending-notifications",
            "schedule": crontab(hour=5, minute=5),  # 00:05 EST in UTC
            "options": {"queue": QueueNames.PERIODIC},
        },
        "create-nightly-billing": {
            "task": "create-nightly-billing",
            "schedule": crontab(hour=5, minute=15),  # 00:15 EST in UTC
            "options": {"queue": QueueNames.REPORTING},
        },
        "create-nightly-notification-status": {
            "task": "create-nightly-notification-status",
            "schedule": crontab(hour=5, minute=30),  # 00:30 EST in UTC, after 'timeout-sending-notifications'
            "options": {"queue": QueueNames.REPORTING},
        },
        "delete-sms-notifications": {
            "task": "delete-sms-notifications",
            "schedule": crontab(hour=9, minute=15),  # 4:15 EST in UTC,  after 'create-nightly-notification-status'
            "options": {"queue": QueueNames.PERIODIC},
        },
        "delete-email-notifications": {
            "task": "delete-email-notifications",
            "schedule": crontab(hour=9, minute=30),  # 4:30 EST in UTC, after 'create-nightly-notification-status'
            "options": {"queue": QueueNames.PERIODIC},
        },
        "delete-letter-notifications": {
            "task": "delete-letter-notifications",
            "schedule": crontab(hour=9, minute=45),  # 4:45 EST in UTC, after 'create-nightly-notification-status'
            "options": {"queue": QueueNames.PERIODIC},
        },
        "delete-inbound-sms": {
            "task": "delete-inbound-sms",
            "schedule": crontab(hour=6, minute=40),  # 1:40 EST in UTC
            "options": {"queue": QueueNames.PERIODIC},
        },
        "send-daily-performance-platform-stats": {
            "task": "send-daily-performance-platform-stats",
            "schedule": crontab(hour=7, minute=0),  # 2:00 EST in UTC
            "options": {"queue": QueueNames.PERIODIC},
        },
        "remove_transformed_dvla_files": {
            "task": "remove_transformed_dvla_files",
            "schedule": crontab(hour=8, minute=40),  # 3:40 EST in UTC
            "options": {"queue": QueueNames.PERIODIC},
        },
        "remove_sms_email_jobs": {
            "task": "remove_sms_email_jobs",
            "schedule": crontab(hour=9, minute=0),  # 4:00 EST in UTC
            "options": {"queue": QueueNames.PERIODIC},
        },
        # quarterly queue
        "insert-quarter-data-for-annual-limits-q1": {
            "task": "insert-quarter-data-for-annual-limits",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=1, month_of_year=7
            ),  # Running this at the end of the day on 1st July
            "options": {"queue": QueueNames.PERIODIC},
        },
        "insert-quarter-data-for-annual-limits-q2": {
            "task": "insert-quarter-data-for-annual-limits",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=1, month_of_year=10
            ),  # Running this at the end of the day on 1st Oct
            "options": {"queue": QueueNames.PERIODIC},
        },
        "insert-quarter-data-for-annual-limits-q3": {
            "task": "insert-quarter-data-for-annual-limits",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=1, month_of_year=1
            ),  # Running this at the end of the day on 1st Jan
            "options": {"queue": QueueNames.PERIODIC},
        },
        "insert-quarter-data-for-annual-limits-q4": {
            "task": "insert-quarter-data-for-annual-limits",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=1, month_of_year=4
            ),  # Running this at the end of the day on 1st April
            "options": {"queue": QueueNames.PERIODIC},
        },
        "send-quarterly-email-q1": {
            "task": "send-quarterly-email",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=2, month_of_year=7
            ),  # Running this at the end of the day on 2nd July
            "options": {"queue": QueueNames.PERIODIC},
        },
        "send-quarterly-email-q2": {
            "task": "send-quarterly-email",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=2, month_of_year=10
            ),  # Running this at the end of the day on 2nd Oct
            "options": {"queue": QueueNames.PERIODIC},
        },
        "send-quarterly-email-q3": {
            "task": "send-quarterly-email",
            "schedule": crontab(
                minute=0, hour=23, day_of_month=3, month_of_year=1
            ),  # Running this at the end of the day on 2nd Jan
            "options": {"queue": QueueNames.PERIODIC},
        },
    }

    CELERY_QUEUES: List[Any] = []
    CELERY_DELIVER_SMS_RATE_LIMIT = os.getenv("CELERY_DELIVER_SMS_RATE_LIMIT", "1/s")
    AWS_SEND_SMS_BOTO_CALL_LATENCY = os.getenv("AWS_SEND_SMS_BOTO_CALL_LATENCY", 0.06)  # average delay in production

    CONTACT_FORM_EMAIL_ADDRESS = os.getenv("CONTACT_FORM_EMAIL_ADDRESS", "helpdesk@cds-snc.ca")
    SENSITIVE_SERVICE_EMAIL = os.getenv("SENSITIVE_SERVICE_EMAIL", "ESDC.Support.CDS-SNC.Soutien.EDSC@servicecanada.gc.ca")

    FROM_NUMBER = "development"

    STATSD_HOST = os.getenv("STATSD_HOST")  # CloudWatch agent, shared with embedded metrics
    STATSD_PORT = 8125
    STATSD_ENABLED = bool(STATSD_HOST)

    SENDING_NOTIFICATIONS_TIMEOUT_PERIOD = 259_200  # 3 days

    SIMULATED_EMAIL_ADDRESSES = (
        "simulate-delivered@notification.canada.ca",
        "simulate-delivered-2@notification.canada.ca",
        "simulate-delivered-3@notification.canada.ca",
    )

    SIMULATED_SMS_NUMBERS = ("+16132532222", "+16132532223", "+16132532224")

    # Match with scripts/internal_stress_test/internal_stress_test.py
    INTERNAL_TEST_NUMBER = "+16135550123"
    EXTERNAL_TEST_NUMBER = "+16135550124"
    INTERNAL_TEST_EMAIL_ADDRESS = "internal.test@cds-snc.ca"

    DVLA_BUCKETS = {
        "job": "{}-dvla-file-per-job".format(os.getenv("NOTIFY_ENVIRONMENT", "development")),
        "notification": "{}-dvla-letter-api-files".format(os.getenv("NOTIFY_ENVIRONMENT", "development")),
    }
    SERVICE_ANNUAL_EMAIL_LIMIT = env.int("SERVICE_ANNUAL_EMAIL_LIMIT", 20_000_000)
    SERVICE_ANNUAL_SMS_LIMIT = env.int("SERVICE_ANNUAL_SMS_LIMIT", 100_000)

    FREE_SMS_TIER_FRAGMENT_COUNT = 250000

    ROUTE_SECRET_KEY_1 = os.getenv("ROUTE_SECRET_KEY_1", "")
    ROUTE_SECRET_KEY_2 = os.getenv("ROUTE_SECRET_KEY_2", "")

    # Format is as follows:
    # {"dataset_1": "token_1", ...}
    PERFORMANCE_PLATFORM_ENDPOINTS = json.loads(os.getenv("PERFORMANCE_PLATFORM_ENDPOINTS", "{}"))

    TEMPLATE_PREVIEW_API_HOST = os.getenv("TEMPLATE_PREVIEW_API_HOST", "http://localhost:6013")
    TEMPLATE_PREVIEW_API_KEY = os.getenv("TEMPLATE_PREVIEW_API_KEY", "my-secret-key")

    DOCUMENT_DOWNLOAD_API_HOST = os.getenv("DOCUMENT_DOWNLOAD_API_HOST", "http://localhost:7000")
    DOCUMENT_DOWNLOAD_API_KEY = os.getenv("DOCUMENT_DOWNLOAD_API_KEY", "auth-token")

    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    NOTIFY_LOG_PATH = ""

    FIDO2_SERVER = Fido2Server(
        PublicKeyCredentialRpEntity(os.getenv("FIDO2_DOMAIN", "localhost"), "Notification"),
        verify_origin=lambda x: True,
    )

    HC_EN_SERVICE_ID = os.getenv("HC_EN_SERVICE_ID", "")
    HC_FR_SERVICE_ID = os.getenv("HC_FR_SERVICE_ID", "")
    BULK_SEND_TEST_SERVICE_ID = os.getenv("BULK_SEND_TEST_SERVICE_ID", "")
    CSV_MAX_ROWS = os.getenv("CSV_MAX_ROWS", 50_000)
    CSV_MAX_ROWS_BULK_SEND = os.getenv("CSV_MAX_ROWS_BULK_SEND", 100_000)
    CSV_BULK_REDIRECT_THRESHOLD = os.getenv("CSV_BULK_REDIRECT_THRESHOLD", 200)

    # Endpoint of Cloudwatch agent running as a side car in EKS listening for embedded metrics
    CLOUDWATCH_AGENT_EMF_PORT = 25888
    CLOUDWATCH_AGENT_ENDPOINT = os.getenv("CLOUDWATCH_AGENT_ENDPOINT", f"tcp://{STATSD_HOST}:{CLOUDWATCH_AGENT_EMF_PORT}")

    # Bounce Rate parameters
    BR_VOLUME_MINIMUM = 1000
    BR_WARNING_PERCENTAGE = 0.05
    BR_CRITICAL_PERCENTAGE = 0.1

    # Feature flags for bounce rate
    # Timestamp in epoch milliseconds to seed the bounce rate. We will seed data for (24, the below config) included.
    FF_BOUNCE_RATE_SEED_EPOCH_MS = os.getenv("FF_BOUNCE_RATE_SEED_EPOCH_MS", False)
    # Feature flag to enable custom retry policies such as lowering retry period for certain priority lanes.
    FF_CELERY_CUSTOM_TASK_PARAMS = env.bool("FF_CELERY_CUSTOM_TASK_PARAMS", True)
    FF_CLOUDWATCH_METRICS_ENABLED = env.bool("FF_CLOUDWATCH_METRICS_ENABLED", False)
    FF_SALESFORCE_CONTACT = env.bool("FF_SALESFORCE_CONTACT", False)
    FF_ANNUAL_LIMIT = env.bool("FF_ANNUAL_LIMIT", False)
    FF_PT_SERVICE_SKIP_FRESHDESK = env.bool("FF_PT_SERVICE_SKIP_FRESHDESK", False)

    # SRE Tools auth keys
    SRE_USER_NAME = "SRE_CLIENT_USER"
    SRE_CLIENT_SECRET = os.getenv("SRE_CLIENT_SECRET")
    # cache clear auth keys
    CACHE_CLEAR_USER_NAME = "CACHE_CLEAR_USER"
    CACHE_CLEAR_CLIENT_SECRET = os.getenv("CACHE_CLEAR_CLIENT_SECRET")
    CYPRESS_AUTH_USER_NAME = "CYPRESS_AUTH_USER"
    CYPRESS_AUTH_CLIENT_SECRET = os.getenv("CYPRESS_AUTH_CLIENT_SECRET")

    # Feature flag for stack trace debugging
    FF_DEBUG_STACK_TRACE = env.bool("FF_DEBUG_STACK_TRACE", False)
    if FF_DEBUG_STACK_TRACE:
        CELERYBEAT_SCHEDULE.update(
            {
                "debug-stack-trace": {
                    "task": "debug-stack-trace",
                    "schedule": 10,
                    "options": {"queue": QueueNames.PERIODIC},
                },
            }
        )

    @classmethod
    def get_sensitive_config(cls) -> list[str]:
        "List of config keys that contain sensitive information"
        return [
            "ADMIN_CLIENT_SECRET",
            "SECRET_KEY",
            "DANGEROUS_SALT",
            "SQLALCHEMY_DATABASE_URI",
            "SQLALCHEMY_DATABASE_READER_URI",
            "SQLALCHEMY_BINDS",
            "REDIS_URL",
            "FRESH_DESK_API_KEY",
            "AWS_SES_ACCESS_KEY",
            "AWS_SES_SECRET_KEY",
            "ROUTE_SECRET_KEY_1",
            "ROUTE_SECRET_KEY_2",
            "SALESFORCE_PASSWORD",
            "SALESFORCE_SECURITY_TOKEN",
            "TEMPLATE_PREVIEW_API_KEY",
            "DOCUMENT_DOWNLOAD_API_KEY",
            "SRE_CLIENT_SECRET",
        ]

    @classmethod
    def get_safe_config(cls) -> dict[str, Any]:
        "Returns a dict of config keys and values with sensitive values masked"
        return logging.get_class_attrs(cls, cls.get_sensitive_config())


######################
# Config overrides ###
######################


class Development(Config):
    DEBUG = True

    # CSV_UPLOAD_BUCKET_NAME = 'development-notifications-csv-upload'
    TEST_LETTERS_BUCKET_NAME = "development-test-letters"
    DVLA_RESPONSE_BUCKET_NAME = "notify.tools-ftp"
    LETTERS_PDF_BUCKET_NAME = "development-letters-pdf"
    LETTERS_SCAN_BUCKET_NAME = "development-letters-scan"
    INVALID_PDF_BUCKET_NAME = "development-letters-invalid-pdf"
    TRANSIENT_UPLOADED_LETTERS = "development-transient-uploaded-letters"

    ADMIN_CLIENT_SECRET = os.getenv("ADMIN_CLIENT_SECRET", "dev-notify-secret-key")
    SECRET_KEY = env.list("SECRET_KEY", ["dev-notify-secret-key"])
    DANGEROUS_SALT = os.getenv("DANGEROUS_SALT", "dev-notify-salt ")
    SRE_CLIENT_SECRET = os.getenv("SRE_CLIENT_SECRET", "dev-notify-secret-key")
    CACHE_CLEAR_CLIENT_SECRET = os.getenv("CACHE_CLEAR_CLIENT_SECRET", "dev-notify-cache-client-secret")
    CYPRESS_AUTH_CLIENT_SECRET = os.getenv("CYPRESS_AUTH_CLIENT_SECRET", "dev-notify-cypress-secret-key")
    CYPRESS_USER_PW_SECRET = os.getenv("CYPRESS_USER_PW_SECRET", "dev-notify-cypress-secret-key")

    NOTIFY_ENVIRONMENT = "development"
    NOTIFICATION_QUEUE_PREFIX = os.getenv("NOTIFICATION_QUEUE_PREFIX", "notification-canada-ca")
    NOTIFY_EMAIL_DOMAIN = os.getenv("NOTIFY_EMAIL_DOMAIN", "notification.canada.ca")

    SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI", "postgresql://postgres@localhost/notification_api")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_PUBLISH_URL = os.getenv("REDIS_PUBLISH_URL", REDIS_URL)

    ANTIVIRUS_ENABLED = env.bool("ANTIVIRUS_ENABLED", False)

    for queue in QueueNames.all_queues():
        Config.CELERY_QUEUES.append(Queue(queue, Exchange("default"), routing_key=queue))

    API_HOST_NAME = "http://localhost:6011"
    API_RATE_LIMIT_ENABLED = True


class Test(Development):
    NOTIFY_EMAIL_DOMAIN = os.getenv("NOTIFY_EMAIL_DOMAIN", "notification.canada.ca")
    FROM_NUMBER = "testing"
    NOTIFY_ENVIRONMENT = "test"
    TESTING = True

    # CSV_UPLOAD_BUCKET_NAME = 'test-notifications-csv-upload'
    TEST_LETTERS_BUCKET_NAME = "test-test-letters"
    DVLA_RESPONSE_BUCKET_NAME = "test.notify.com-ftp"
    LETTERS_PDF_BUCKET_NAME = "test-letters-pdf"
    LETTERS_SCAN_BUCKET_NAME = "test-letters-scan"
    INVALID_PDF_BUCKET_NAME = "test-letters-invalid-pdf"
    TRANSIENT_UPLOADED_LETTERS = "test-transient-uploaded-letters"

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "SQLALCHEMY_DATABASE_URI",
        "postgresql://postgres@localhost/test_notification_api",
    )

    BROKER_URL = "you-forgot-to-mock-celery-in-your-tests://"

    ANTIVIRUS_ENABLED = True

    for queue in QueueNames.all_queues():
        Config.CELERY_QUEUES.append(Queue(queue, Exchange("default"), routing_key=queue))

    API_RATE_LIMIT_ENABLED = True
    API_HOST_NAME = "http://localhost:6011"

    TEMPLATE_PREVIEW_API_HOST = "http://localhost:9999"
    CRM_GITHUB_PERSONAL_ACCESS_TOKEN = "test-token"
    CRM_ORG_LIST_URL = "https://test-url.com"
    FAILED_LOGIN_LIMIT = 0


class Production(Config):
    NOTIFY_EMAIL_DOMAIN = os.getenv("NOTIFY_EMAIL_DOMAIN", "notification.canada.ca")
    NOTIFY_ENVIRONMENT = "production"
    # CSV_UPLOAD_BUCKET_NAME = 'live-notifications-csv-upload'
    TEST_LETTERS_BUCKET_NAME = "production-test-letters"
    DVLA_RESPONSE_BUCKET_NAME = "notifications.service.gov.uk-ftp"
    LETTERS_PDF_BUCKET_NAME = "production-letters-pdf"
    LETTERS_SCAN_BUCKET_NAME = "production-letters-scan"
    INVALID_PDF_BUCKET_NAME = "production-letters-invalid-pdf"
    TRANSIENT_UPLOADED_LETTERS = "production-transient-uploaded-letters"
    FROM_NUMBER = "CANADA.CA"
    PERFORMANCE_PLATFORM_ENABLED = False
    API_RATE_LIMIT_ENABLED = True
    CHECK_PROXY_HEADER = False
    CRONITOR_ENABLED = False


class Staging(Production):
    NOTIFY_ENVIRONMENT = "staging"


class Scratch(Production):
    NOTIFY_ENVIRONMENT = "scratch"


class Dev(Production):
    NOTIFY_ENVIRONMENT = "dev"


configs = {
    "development": Development,
    "test": Test,
    "production": Production,
    "staging": Staging,
    "scratch": Scratch,
    "dev": Dev,
}
