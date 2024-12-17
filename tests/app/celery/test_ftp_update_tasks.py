from collections import namedtuple
from datetime import date

import pytest
from tests.app.db import create_notification, save_notification

from app.celery.tasks import check_billable_units, get_billing_date_in_est_from_filename


@pytest.fixture
def notification_update():
    """
    Returns a namedtuple to use as the argument for the check_billable_units function
    """
    NotificationUpdate = namedtuple("NotificationUpdate", ["reference", "status", "page_count", "cost_threshold"])
    return NotificationUpdate("REFERENCE_ABC", "sent", "1", "cost")


def test_check_billable_units_when_billable_units_matches_page_count(client, sample_letter_template, mocker, notification_update):
    mock_logger = mocker.patch("app.celery.tasks.current_app.logger.error")

    save_notification(create_notification(sample_letter_template, reference="REFERENCE_ABC", billable_units=1))

    check_billable_units(notification_update)

    mock_logger.assert_not_called()


def test_check_billable_units_when_billable_units_does_not_match_page_count(
    client, sample_letter_template, mocker, notification_update
):
    mock_logger = mocker.patch("app.celery.tasks.current_app.logger.exception")

    notification = save_notification(create_notification(sample_letter_template, reference="REFERENCE_ABC", billable_units=3))

    check_billable_units(notification_update)

    mock_logger.assert_called_once_with(
        "Notification with id {} has 3 billable_units but DVLA says page count is 1".format(notification.id)
    )


@pytest.mark.parametrize(
    "filename_date, billing_date",
    [("20170820230000", date(2017, 8, 20)), ("20170120230000", date(2017, 1, 20))],
)
def test_get_billing_date_in_est_from_filename(filename_date, billing_date):
    filename = "NOTIFY-{}-RSP.TXT".format(filename_date)
    result = get_billing_date_in_est_from_filename(filename)

    assert result == billing_date
