"""
Revision ID: 0425_bounce_rate_limits

Revises: 0424_sms_templates_in_redacted

Create Date: 2022-12-28 00:00:00
"""

from datetime import datetime

from alembic import op
from flask import current_app

revision = "0425_service_suspend_resume"
down_revision = "0424_sms_templates_in_redacted"

bounce_rate_exceeded = current_app.config["BOUNCE_RATE_EXCEEDED_ID"]
bounce_rate_warning = current_app.config["BOUNCE_RATE_WARNING_ID"]
resume_service = current_app.config["SERVICE_RESUMED_ID"]

templates = [
    {
        "id": bounce_rate_exceeded,
        "name": "Bounce Rate Exceeded",
        "type": "email",
        "subject": "We’ve suspended your service",
        "content_lines": [
            "The email bounce rate has reached 10% for “((service_name))”. We’ve suspended the service to maintain our operations. While the service is under suspension, it will not be able to send email or text messages.",
            "",
            "You need to update and verify your list of recipient’s email addresses. Once you’ve taken these steps, you can request to resume service by [contacting us]((contact_us_url)). ",
            "",
            "The bounce rate for each service on GC Notify contributes to our total bounce rate. A high bounce rate suggests we’re emailing recipients without their consent. Then email providers send messages from GC Notify to the spam folder.",
            "",
            "An email may bounce if:",
            "",
            "(1) The recipient or their email provider has blocked sends from your service.",
            "(2) You send to an email address that does not exist." "",
            "The GC Notify team",
        ],
    },
    {
        "id": bounce_rate_warning,
        "name": "Bounce Rate Warning",
        "type": "email",
        "subject": "Your bounce rate has exceeded 5%",
        "content_lines": [
            "Hello ((name))," "",
            "The bounce rate has exceeded 5% for “((service_name))”. You should update your list of recipient’s email addresses."
            "",
            "An email may bounce if:",
            "(1) The recipient or their email provider has blocked sends from your service.",
            "(2) You send to an email address that does not exist.",
            "",
            "The bounce rate for each service on GC Notify contributes to our total bounce rate. A high bounce rate suggests we’re emailing recipients without their consent. Then email providers send messages from GC Notify to the spam folder.",
            "",
            "To maintain our operations, we’ll suspend the service if its bounce rate reaches 10%. While the service is under suspension, it will not be able to send email or text messages."
            "",
            "The GC Notify team",
        ],
    },
    {
        "id": resume_service,
        "name": "Resume Service",
        "type": "email",
        "subject": "We’ve resumed your service",
        "content_lines": [
            "Hello ((name)),",
            "",
            "“((service_name))” can send messages again.  We’ve removed the suspension." "",
            "The GC Notify Team",
        ],
    },
]


def upgrade():

    insert = """
    INSERT INTO {} (id, name, template_type, created_at, content, archived, service_id, subject,
    created_by_id, version, process_type, hidden)
    VALUES ('{}', '{}', '{}', current_timestamp, '{}', False, '{}', '{}', '{}', 1, '{}', false)
    """

    for template in templates:
        for table_name in "templates", "templates_history":
            op.execute(
                insert.format(
                    table_name,
                    template["id"],
                    template["name"],
                    template["type"],
                    "\n".join(template["content_lines"]),
                    current_app.config["NOTIFY_SERVICE_ID"],
                    template.get("subject"),
                    current_app.config["NOTIFY_USER_ID"],
                    "normal",
                )
            )
        op.execute(
            f"""
            INSERT INTO template_redacted(
                template_id,
                redact_personalisation,
                updated_at,
                updated_by_id
            ) VALUES ('{template["id"]}', false, current_timestamp, '{current_app.config["NOTIFY_USER_ID"]}')
            """
        )


def downgrade():
    for template in templates:
        for table in "templates", "template_history":
            op.execute(f"DELETE FROM {table} where template_id = '{template['id']}'")
        op.execute(f"DELETE FROM template_redacted WHERE template_id = '{template['id']}'")
