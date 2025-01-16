"""empty message

Revision ID: 0466_add_cypress_data
Revises: 0465_add_constraints
Create Date: 2016-06-01 14:17:01.963181

"""

import hashlib
import uuid

# revision identifiers, used by Alembic.
from datetime import datetime

from alembic import op
from flask import current_app

from app.dao.date_util import get_current_financial_year_start_year
from app.encryption import hashpw
from app.models import PERMISSION_LIST

revision = "0466_add_cypress_data"
down_revision = "0465_add_constraints"

user_id = current_app.config["CYPRESS_TEST_USER_ID"]
admin_user_id = current_app.config["CYPRESS_TEST_USER_ADMIN_ID"]
service_id = current_app.config["CYPRESS_SERVICE_ID"]
email_template_id = current_app.config["CYPRESS_SMOKE_TEST_EMAIL_TEMPLATE_ID"]
sms_template_id = current_app.config["CYPRESS_SMOKE_TEST_SMS_TEMPLATE_ID"]
default_category_id = current_app.config["DEFAULT_TEMPLATE_CATEGORY_LOW"]


def upgrade():
    cypress_user_pw = current_app.config.get("CYPRESS_USER_PW_SECRET")
    if not cypress_user_pw:
        cypress_user_pw = uuid.uuid4().hex[:32]
        current_app.logger.warning(f"CYPRESS_USER_PW_SECRET not set, using random password")

    password = hashpw(hashlib.sha256((cypress_user_pw + current_app.config["DANGEROUS_SALT"]).encode("utf-8")).hexdigest())
    current_year = get_current_financial_year_start_year()
    default_limit = 250000

    op.get_bind()

    # insert test user
    user_insert = """INSERT INTO users (id, name, email_address, created_at, failed_login_count, _password, mobile_number, password_changed_at, state, platform_admin, auth_type)
                     VALUES ('{}', 'Notify UI test user', 'notify-ui-tests+regular_user@cds-snc.ca', '{}', 0,'{}', '+441234123412', '{}',  'active', False, 'email_auth')
                  """
    op.execute(user_insert.format(user_id, datetime.utcnow(), password, datetime.utcnow()))
    # insert test user thats platform admin
    user_insert = """INSERT INTO users (id, name, email_address, created_at, failed_login_count, _password, mobile_number, password_changed_at, state, platform_admin, auth_type)
                     VALUES ('{}', 'Notify UI test user', 'notify-ui-tests+platform_admin@cds-snc.ca', '{}', 0,'{}', '+441234123412', '{}',  'active', True, 'email_auth')
                  """
    op.execute(user_insert.format(admin_user_id, datetime.utcnow(), password, datetime.utcnow()))

    # insert test service
    service_history_insert = """INSERT INTO services_history (id, name, created_at, active, message_limit, restricted, research_mode, email_from, created_by_id, sms_daily_limit, prefix_sms, organisation_type, version)
                        VALUES ('{}', 'Cypress UI Testing Service', '{}', True, 10000, False, False, 'notify@digital.cabinet-office.gov.uk',
                        '{}', 10000, True, 'central', 1)
                     """
    op.execute(service_history_insert.format(service_id, datetime.utcnow(), user_id))
    service_insert = """INSERT INTO services (id, name, created_at, active, message_limit, restricted, research_mode, email_from, created_by_id, sms_daily_limit, prefix_sms, organisation_type, version)
                        VALUES ('{}', 'Cypress UI Testing Service', '{}', True, 10000, False, False, 'notify@digital.cabinet-office.gov.uk',
                        '{}', 10000, True, 'central', 1)
                    """
    op.execute(service_insert.format(service_id, datetime.utcnow(), user_id))

    for send_type in ("sms", "email"):
        service_perms_insert = """INSERT INTO service_permissions (service_id, permission, created_at) 
        VALUES ('{}', '{}', '{}')"""
        op.execute(service_perms_insert.format(service_id, send_type, datetime.utcnow()))

    insert_row_if_not_exist = """INSERT INTO annual_billing (id, service_id, financial_year_start, free_sms_fragment_limit, created_at, updated_at) 
        VALUES ('{}', '{}', {}, {}, '{}', '{}')
    """
    op.execute(
        insert_row_if_not_exist.format(
            uuid.uuid4(), service_id, current_year, default_limit, datetime.utcnow(), datetime.utcnow()
        )
    )

    user_to_service_insert = """INSERT INTO user_to_service (user_id, service_id) VALUES ('{}', '{}')"""
    op.execute(user_to_service_insert.format(user_id, service_id))

    for permission in PERMISSION_LIST:
        perms_insert = (
            """INSERT INTO permissions (id, service_id, user_id, permission, created_at) VALUES ('{}', '{}', '{}', '{}', '{}')"""
        )
        op.execute(perms_insert.format(uuid.uuid4(), service_id, user_id, permission, datetime.utcnow()))

    # insert test email template
    _insert_template(email_template_id, "SMOKE_TEST_EMAIL", "SMOKE_TEST_EMAIL", "email", "SMOKE_TEST_EMAIL", default_category_id)

    # insert test SMS template
    _insert_template(sms_template_id, "SMOKE_TEST_SMS", "SMOKE_TEST_SMS", "sms", None, default_category_id)

    # insert 10 random email templates
    for i in range(10):
        _insert_template(
            uuid.uuid4(), "Template {}".format(i), "Template {}".format(i), "email", "Template {}".format(i), default_category_id
        )

    # insert 1 random sms template
    _insert_template(uuid.uuid4(), "Template 11", "Template 11", "sms", "Template 11", "b6c42a7e-2a26-4a07-802b-123a5c3198a9")


def downgrade():
    op.get_bind()
    op.execute("delete from permissions where service_id = '{}'".format(service_id))
    op.execute("delete from annual_billing where service_id = '{}'".format(service_id))
    op.execute("delete from service_permissions where service_id = '{}'".format(service_id))
    op.execute("delete from login_events where user_id = '{}'".format(user_id))
    op.execute("delete from verify_codes where user_id = '{}'".format(user_id))
    op.execute("delete from login_events where user_id = '{}'".format(admin_user_id))
    op.execute("delete from verify_codes where user_id = '{}'".format(admin_user_id))
    op.execute("delete from templates where service_id = '{}'".format(service_id))
    op.execute("delete from templates_history where service_id = '{}'".format(service_id))
    op.execute("delete from user_to_service where service_id = '{}'".format(service_id))
    op.execute("delete from services_history where id = '{}'".format(service_id))
    op.execute("delete from services where id = '{}'".format(service_id))
    op.execute("delete from users where id = '{}'".format(user_id))
    op.execute("delete from users where id = '{}'".format(admin_user_id))


def _insert_template(id, name, content, type, subject, category_id):
    template_history_insert = """INSERT INTO templates_history (id, name, template_type, created_at,
                                                                content, archived, service_id,
                                                                subject, created_by_id, hidden, template_category_id, version)
                                 VALUES ('{}', '{}', '{}', '{}', '{}', False, '{}', '{}', '{}', False, '{}', 1)
                              """
    template_insert = """INSERT INTO templates (id, name, template_type, created_at,
                                                content, archived, service_id, subject, created_by_id, hidden, template_category_id, version)
                                 VALUES ('{}', '{}', '{}', '{}', '{}', False, '{}', '{}', '{}', False, '{}', 1)
                              """

    op.execute(
        template_history_insert.format(
            uuid.uuid4(), name, type, datetime.utcnow(), content, service_id, subject, user_id, category_id
        )
    )
    op.execute(template_insert.format(id, name, type, datetime.utcnow(), content, service_id, subject, user_id, category_id))
