import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from freezegun import freeze_time
from notifications_utils.timezones import convert_utc_to_local_timezone
from tests.app.db import (
    create_ft_notification_status,
    create_letter_rate,
    create_notification,
    create_notification_history,
    create_rate,
    create_service,
    create_template,
    create_user,
    save_notification,
)
from tests.conftest import set_config

from app import annual_limit_client
from app.celery.reporting_tasks import (
    create_nightly_billing,
    create_nightly_billing_for_day,
    create_nightly_notification_status,
    create_nightly_notification_status_for_day,
    insert_quarter_data_for_annual_limits,
    send_quarter_email,
)
from app.dao.fact_billing_dao import get_rate
from app.models import (
    EMAIL_TYPE,
    LETTER_TYPE,
    SMS_TYPE,
    AnnualLimitsData,
    FactBilling,
    FactNotificationStatus,
    Notification,
)


def mocker_get_rate(
    non_letter_rates,
    letter_rates,
    notification_type,
    bst_date,
    crown=None,
    rate_multiplier=None,
    post_class="second",
):
    if notification_type == LETTER_TYPE:
        return Decimal(2.1)
    elif notification_type == SMS_TYPE:
        return Decimal(1.33)
    elif notification_type == EMAIL_TYPE:
        return Decimal(0)


@freeze_time("2019-08-01T04:30:00")
@pytest.mark.parametrize(
    "day_start, expected_kwargs",
    [
        (None, ["2019-07-31", "2019-07-30", "2019-07-29", "2019-07-28"]),
        ("2019-07-21", ["2019-07-21", "2019-07-20", "2019-07-19", "2019-07-18"]),
    ],
)
def test_create_nightly_billing_triggers_tasks_for_days(notify_api, mocker, day_start, expected_kwargs):
    mock_celery = mocker.patch("app.celery.reporting_tasks.create_nightly_billing_for_day")
    create_nightly_billing(day_start)

    assert mock_celery.apply_async.call_count == 4
    for i in range(4):
        assert mock_celery.apply_async.call_args_list[i][1]["kwargs"] == {"process_day": expected_kwargs[i]}


@freeze_time("2019-08-01T04:30:00")
@pytest.mark.parametrize(
    "day_start, expected_kwargs",
    [
        (None, ["2019-07-31", "2019-07-30", "2019-07-29", "2019-07-28"]),
        ("2019-07-21", ["2019-07-21", "2019-07-20", "2019-07-19", "2019-07-18"]),
    ],
)
def test_create_nightly_notification_status_triggers_tasks_for_days(notify_api, mocker, day_start, expected_kwargs):
    mock_celery = mocker.patch("app.celery.reporting_tasks.create_nightly_notification_status_for_day")
    create_nightly_notification_status(day_start)

    assert mock_celery.apply_async.call_count == 4
    for i in range(4):
        assert mock_celery.apply_async.call_args_list[i][1]["kwargs"] == {"process_day": expected_kwargs[i]}


@pytest.mark.parametrize(
    "second_rate, records_num, billable_units, multiplier",
    [(1.0, 1, 2, [1]), (2.0, 2, 1, [1, 2])],
)
def test_create_nightly_billing_for_day_sms_rate_multiplier(
    sample_service,
    sample_template,
    mocker,
    second_rate,
    records_num,
    billable_units,
    multiplier,
):
    yesterday = convert_utc_to_local_timezone((datetime.now() - timedelta(days=1))).replace(hour=12, minute=00)

    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    # These are sms notifications
    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_template,
            status="delivered",
            sent_by="sns",
            international=False,
            rate_multiplier=1.0,
            billable_units=1,
        )
    )
    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_template,
            status="delivered",
            sent_by="sns",
            international=False,
            rate_multiplier=second_rate,
            billable_units=1,
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0

    # Celery expects the arguments to be a string or primitive type.
    yesterday_str = datetime.strftime(yesterday, "%Y-%m-%d")
    create_nightly_billing_for_day(yesterday_str)
    records = FactBilling.query.order_by("rate_multiplier").all()
    assert len(records) == records_num
    for i, record in enumerate(records):
        assert record.bst_date == datetime.date(yesterday)
        assert record.rate == Decimal(1.33)
        assert record.billable_units == billable_units
        assert record.rate_multiplier == multiplier[i]


def test_create_nightly_billing_for_day_different_templates(sample_service, sample_template, sample_email_template, mocker):
    yesterday = convert_utc_to_local_timezone((datetime.now() - timedelta(days=1))).replace(hour=12, minute=00)

    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_template,
            status="delivered",
            sent_by="sns",
            international=False,
            rate_multiplier=1.0,
            billable_units=1,
        )
    )
    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_email_template,
            status="delivered",
            sent_by="ses",
            international=False,
            rate_multiplier=0,
            billable_units=0,
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0
    # Celery expects the arguments to be a string or primitive type.
    yesterday_str = datetime.strftime(yesterday, "%Y-%m-%d")
    create_nightly_billing_for_day(yesterday_str)
    records = FactBilling.query.order_by("rate_multiplier").all()

    assert len(records) == 2
    multiplier = [0, 1]
    billable_units = [0, 1]
    rate = [0, Decimal(1.33)]
    for i, record in enumerate(records):
        assert record.bst_date == datetime.date(yesterday)
        assert record.rate == rate[i]
        assert record.billable_units == billable_units[i]
        assert record.rate_multiplier == multiplier[i]


def test_create_nightly_billing_for_day_different_sent_by(sample_service, sample_template, sample_email_template, mocker):
    yesterday = convert_utc_to_local_timezone((datetime.now() - timedelta(days=1))).replace(hour=12, minute=00)

    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    # These are sms notifications
    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_template,
            status="delivered",
            sent_by="sns",
            international=False,
            rate_multiplier=1.0,
            billable_units=1,
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0

    # Celery expects the arguments to be a string or primitive type.
    yesterday_str = datetime.strftime(yesterday, "%Y-%m-%d")
    create_nightly_billing_for_day(yesterday_str)
    records = FactBilling.query.order_by("rate_multiplier").all()

    assert len(records) == 1
    for i, record in enumerate(records):
        assert record.bst_date == datetime.date(yesterday)
        assert record.rate == Decimal(1.33)
        assert record.billable_units == 1
        assert record.rate_multiplier == 1.0


def test_create_nightly_billing_for_day_different_letter_postage(notify_db_session, sample_letter_template, mocker):
    yesterday = convert_utc_to_local_timezone((datetime.now() - timedelta(days=1))).replace(hour=12, minute=00)
    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    for i in range(2):
        save_notification(
            create_notification(
                created_at=yesterday,
                template=sample_letter_template,
                status="delivered",
                sent_by="dvla",
                billable_units=2,
                postage="first",
            )
        )
    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_letter_template,
            status="delivered",
            sent_by="dvla",
            billable_units=2,
            postage="second",
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0
    # Celery expects the arguments to be a string or primitive type.
    yesterday_str = datetime.strftime(yesterday, "%Y-%m-%d")
    create_nightly_billing_for_day(yesterday_str)

    records = FactBilling.query.order_by("postage").all()
    assert len(records) == 2
    assert records[0].notification_type == LETTER_TYPE
    assert records[0].bst_date == datetime.date(yesterday)
    assert records[0].postage == "first"
    assert records[0].notifications_sent == 2
    assert records[0].billable_units == 4

    assert records[1].notification_type == LETTER_TYPE
    assert records[1].bst_date == datetime.date(yesterday)
    assert records[1].postage == "second"
    assert records[1].notifications_sent == 1
    assert records[1].billable_units == 2


def test_create_nightly_billing_for_day_letter(sample_service, sample_letter_template, mocker):
    yesterday = convert_utc_to_local_timezone((datetime.now() - timedelta(days=1))).replace(hour=12, minute=00)

    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_letter_template,
            status="delivered",
            sent_by="dvla",
            international=False,
            rate_multiplier=2.0,
            billable_units=2,
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0
    # Celery expects the arguments to be a string or primitive type.
    yesterday_str = datetime.strftime(yesterday, "%Y-%m-%d")
    create_nightly_billing_for_day(yesterday_str)
    records = FactBilling.query.order_by("rate_multiplier").all()
    assert len(records) == 1
    record = records[0]
    assert record.notification_type == LETTER_TYPE
    assert record.bst_date == datetime.date(yesterday)
    assert record.rate == Decimal(2.1)
    assert record.billable_units == 2
    assert record.rate_multiplier == 2.0


def test_create_nightly_billing_for_day_null_sent_by_sms(sample_service, sample_template, mocker):
    yesterday = convert_utc_to_local_timezone((datetime.now() - timedelta(days=1))).replace(hour=12, minute=00)

    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    save_notification(
        create_notification(
            created_at=yesterday,
            template=sample_template,
            status="delivered",
            sent_by=None,
            international=False,
            rate_multiplier=1.0,
            billable_units=1,
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0

    # Celery expects the arguments to be a string or primitive type.
    yesterday_str = datetime.strftime(yesterday, "%Y-%m-%d")
    create_nightly_billing_for_day(yesterday_str)
    records = FactBilling.query.all()

    assert len(records) == 1
    record = records[0]
    assert record.bst_date == datetime.date(yesterday)
    assert record.rate == Decimal(1.33)
    assert record.billable_units == 1
    assert record.rate_multiplier == 1
    assert record.provider == "unknown"


def test_get_rate_for_letter_latest(notify_db_session):
    # letter rates should be passed into the get_rate function as a tuple of start_date, crown, sheet_count,
    # rate and post_class
    new = create_letter_rate(datetime(2017, 12, 1), crown=True, sheet_count=1, rate=0.33, post_class="second")
    old = create_letter_rate(datetime(2016, 12, 1), crown=True, sheet_count=1, rate=0.30, post_class="second")
    letter_rates = [new, old]

    rate = get_rate([], letter_rates, LETTER_TYPE, date(2018, 1, 1), True, 1)
    assert rate == Decimal("0.33")


def test_get_rate_for_sms_and_email(notify_db_session):
    non_letter_rates = [
        create_rate(datetime(2017, 12, 1), 0.15, SMS_TYPE),
        create_rate(datetime(2017, 12, 1), 0, EMAIL_TYPE),
    ]

    rate = get_rate(non_letter_rates, [], SMS_TYPE, date(2018, 1, 1))
    assert rate == Decimal(0.15)

    rate = get_rate(non_letter_rates, [], EMAIL_TYPE, date(2018, 1, 1))
    assert rate == Decimal(0)


@freeze_time("2018-03-30T05:00:00")
# summer time starts on 2018-03-25
def test_create_nightly_billing_for_day_use_BST(sample_service, sample_template, mocker):
    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    # too late
    save_notification(
        create_notification(
            created_at=datetime(2018, 3, 25, 23, 1),
            template=sample_template,
            status="delivered",
            rate_multiplier=1.0,
            billable_units=1,
        )
    )

    save_notification(
        create_notification(
            created_at=datetime(2018, 3, 25, 22, 59),
            template=sample_template,
            status="delivered",
            rate_multiplier=1.0,
            billable_units=2,
        )
    )

    # too early
    save_notification(
        create_notification(
            created_at=datetime(2018, 3, 24, 23, 59),
            template=sample_template,
            status="delivered",
            rate_multiplier=1.0,
            billable_units=4,
        )
    )

    assert Notification.query.count() == 3
    assert FactBilling.query.count() == 0

    create_nightly_billing_for_day("2018-03-25")
    records = FactBilling.query.order_by(FactBilling.bst_date).all()

    assert len(records) == 1
    assert records[0].bst_date == date(2018, 3, 25)
    assert records[0].billable_units == 3


@freeze_time("2018-01-15T03:30:00")
@pytest.mark.skip(reason="Not in use")
def test_create_nightly_billing_for_day_update_when_record_exists(sample_service, sample_template, mocker):
    mocker.patch("app.dao.fact_billing_dao.get_rate", side_effect=mocker_get_rate)

    save_notification(
        create_notification(
            created_at=datetime.now() - timedelta(days=1),
            template=sample_template,
            status="delivered",
            sent_by=None,
            international=False,
            rate_multiplier=1.0,
            billable_units=1,
        )
    )

    records = FactBilling.query.all()
    assert len(records) == 0

    create_nightly_billing_for_day("2018-01-14")
    records = FactBilling.query.order_by(FactBilling.bst_date).all()

    assert len(records) == 1
    assert records[0].bst_date == date(2018, 1, 13)
    assert records[0].billable_units == 1
    assert not records[0].updated_at

    save_notification(
        create_notification(
            created_at=datetime.now() - timedelta(days=1),
            template=sample_template,
            status="delivered",
            sent_by=None,
            international=False,
            rate_multiplier=1.0,
            billable_units=1,
        )
    )

    # run again, make sure create_nightly_billing() updates with no error
    create_nightly_billing_for_day("2018-01-14")
    assert len(records) == 1
    assert records[0].billable_units == 2
    assert records[0].updated_at


@freeze_time("2019-01-05")
def test_create_nightly_notification_status_for_day(notify_db_session):
    first_service = create_service(service_name="First Service")
    first_template = create_template(service=first_service)
    second_service = create_service(service_name="second Service")
    second_template = create_template(service=second_service, template_type="email")
    third_service = create_service(service_name="third Service")
    third_template = create_template(service=third_service, template_type="letter")

    save_notification(create_notification(template=first_template, status="delivered"))
    save_notification(
        create_notification(
            template=first_template,
            status="delivered",
            created_at=datetime(2019, 1, 1, 12, 0),
        )
    )

    save_notification(create_notification(template=second_template, status="temporary-failure"))
    save_notification(
        create_notification(
            template=second_template,
            status="temporary-failure",
            created_at=datetime(2019, 1, 1, 12, 0),
        )
    )

    save_notification(create_notification(template=third_template, status="created", billable_units=100))
    save_notification(
        create_notification(
            template=third_template,
            status="created",
            created_at=datetime(2019, 1, 1, 12, 0),
            billable_units=100,
        )
    )

    assert len(FactNotificationStatus.query.all()) == 0

    create_nightly_notification_status_for_day("2019-01-01")

    new_data = FactNotificationStatus.query.all()

    assert len(new_data) == 3
    assert new_data[0].bst_date == date(2019, 1, 1)
    assert new_data[1].bst_date == date(2019, 1, 1)
    assert new_data[2].bst_date == date(2019, 1, 1)
    assert new_data[2].billable_units == 100


@freeze_time("2019-01-05")
def test_ensure_create_nightly_notification_status_for_day_copies_billable_units(notify_db_session):
    first_service = create_service(service_name="First Service")
    first_template = create_template(service=first_service)
    second_service = create_service(service_name="second Service")
    second_template = create_template(service=second_service, template_type="email")

    save_notification(
        create_notification(
            template=first_template,
            status="delivered",
            created_at=datetime(2019, 1, 1, 12, 0),
            billable_units=5,
        )
    )

    save_notification(
        create_notification(
            template=second_template,
            status="temporary-failure",
            created_at=datetime(2019, 1, 1, 12, 0),
            billable_units=10,
        )
    )

    assert len(FactNotificationStatus.query.all()) == 0

    create_nightly_notification_status_for_day("2019-01-01")

    new_data = FactNotificationStatus.query.all()

    assert len(new_data) == 2
    assert new_data[0].billable_units == 5
    assert new_data[1].billable_units == 10


@freeze_time("2019-01-05T06:00:00")
def test_ensure_create_nightly_notification_status_for_day_copies_billable_units_from_notificationsHistory(notify_db_session):
    first_service = create_service(service_name="First Service")
    first_template = create_template(service=first_service)
    second_service = create_service(service_name="second Service")
    second_template = create_template(service=second_service, template_type="email")

    create_notification_history(template=first_template, billable_units=5)
    create_notification_history(template=second_template, billable_units=10)

    assert len(FactNotificationStatus.query.all()) == 0

    create_nightly_notification_status_for_day("2019-01-05")

    new_data = FactNotificationStatus.query.all()

    assert len(new_data) == 2
    assert new_data[0].billable_units == 5
    assert new_data[1].billable_units == 10


# the job runs at 12:30am London time. 04/01 is in BST.
@freeze_time("2019-04-01T5:30")
def test_create_nightly_notification_status_for_day_respects_local_timezone(
    sample_template,
):
    save_notification(create_notification(sample_template, status="delivered", created_at=datetime(2019, 4, 2, 5, 0)))  # too new

    save_notification(create_notification(sample_template, status="created", created_at=datetime(2019, 4, 2, 6, 59)))
    save_notification(create_notification(sample_template, status="created", created_at=datetime(2019, 4, 1, 5, 59)))

    save_notification(
        create_notification(sample_template, status="delivered", created_at=datetime(2019, 3, 30, 3, 59))
    )  # too old

    create_nightly_notification_status_for_day("2019-04-01")

    noti_status = FactNotificationStatus.query.order_by(FactNotificationStatus.bst_date).all()
    print(noti_status)
    assert len(noti_status) == 1

    assert noti_status[0].bst_date == date(2019, 4, 1)
    assert noti_status[0].notification_status == "created"


@freeze_time("2019-04-01T5:30")
def test_create_nightly_notification_status_for_day_clears_failed_delivered_notification_counts(
    sample_template, notify_api, mocker
):
    service_ids = []
    for i in range(39):
        user = create_user(email=f"test{i}@test.ca", mobile_number=f"{i}234567890")
        service = create_service(service_id=uuid.uuid4(), service_name=f"service{i}", user=user, email_from=f"best.email{i}")
        template_sms = create_template(service=service)
        template_email = create_template(service=service, template_type="email")

        save_notification(create_notification(template_sms, status="delivered", created_at=datetime(2019, 4, 1, 5, 0)))
        save_notification(create_notification(template_email, status="delivered", created_at=datetime(2019, 4, 1, 5, 0)))
        save_notification(create_notification(template_sms, status="failed", created_at=datetime(2019, 4, 1, 5, 0)))
        save_notification(create_notification(template_email, status="failed", created_at=datetime(2019, 4, 1, 5, 0)))

        mapping = {"sms_failed": 1, "sms_delivered": 1, "email_failed": 1, "email_delivered": 1}
        annual_limit_client.seed_annual_limit_notifications(service.id, mapping)
        service_ids.append(service.id)

    with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
        create_nightly_notification_status_for_day("2019-04-01")

    for service_id in service_ids:
        assert all(value == 0 for value in annual_limit_client.get_all_notification_counts(service_id).values())


class TestInsertQuarterData:
    def test_insert_quarter_data(self, notify_db_session):
        service_1 = create_service(service_name="service_1")
        service_2 = create_service(service_name="service_2")

        create_ft_notification_status(date(2018, 1, 1), "sms", service_1, count=4)
        create_ft_notification_status(date(2018, 5, 2), "sms", service_1, count=10)
        create_ft_notification_status(date(2018, 3, 20), "sms", service_2, count=100)
        create_ft_notification_status(date(2018, 2, 1), "sms", service_2, count=1000)

        # Data for Q4 2017
        insert_quarter_data_for_annual_limits(datetime(2018, 4, 1))

        assert AnnualLimitsData.query.count() == 2
        assert AnnualLimitsData.query.filter_by(service_id=service_1.id).first().notification_count == 4
        assert AnnualLimitsData.query.filter_by(service_id=service_2.id).first().notification_count == 1100
        assert AnnualLimitsData.query.filter_by(service_id=service_1.id).first().time_period == "Q4-2017"

        # Data for Q1 2018
        insert_quarter_data_for_annual_limits(datetime(2018, 7, 1))
        assert AnnualLimitsData.query.filter_by(service_id=service_1.id, time_period="Q1-2018").first().notification_count == 10


class TestSendQuarterEmail:
    def test_send_quarter_email(self, sample_user, mocker, notify_db_session):
        service_1 = create_service(service_name="service_1")
        service_2 = create_service(service_name="service_2")

        create_ft_notification_status(date(2018, 1, 1), "sms", service_1, count=4)
        create_ft_notification_status(date(2018, 5, 2), "sms", service_1, count=10)
        create_ft_notification_status(date(2018, 3, 20), "sms", service_2, count=100)
        create_ft_notification_status(date(2018, 2, 1), "sms", service_2, count=1000)

        # Data for Q4 2017
        insert_quarter_data_for_annual_limits(datetime(2018, 4, 1))

        service_1.users = [sample_user]
        service_2.users = [sample_user]
        send_mock = mocker.patch("app.celery.reporting_tasks.send_annual_usage_data")

        markdown_list_en = "## service_1 \nText messages: you've sent 4 out of 25,000 (0.0%)\n\n## service_2 \nText messages: you've sent 1 100 out of 25 000 (4.0%)\n\n"
        markdown_list_fr = "## service_1 \n\n## service_2 \n\n"
        send_quarter_email(datetime(2018, 4, 1))
        assert send_mock.call_args(
            sample_user.id,
            2018,
            2019,
            markdown_list_en,
            markdown_list_fr,
        )
