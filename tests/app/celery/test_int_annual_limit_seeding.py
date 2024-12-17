import uuid
from datetime import datetime

from freezegun import freeze_time
from tests.app.db import (
    create_notification,
    create_service,
    create_template,
    create_user,
    save_notification,
)
from tests.conftest import set_config

from app import annual_limit_client
from app.aws.mocks import sns_success_callback
from app.celery.process_sns_receipts_tasks import process_sns_results
from app.celery.reporting_tasks import create_nightly_notification_status_for_day


def test_int_annual_limit_seeding_and_incrementation_flows_in_celery(sample_template, notify_api, mocker):
    """
    This integration-style test verifies the annual limit seeding and notification counting flows across multiple days, testing the flow
    between the process_sns_receipts task, which is responsible for seeded and incrementing notification counts in Redis, and the
    create_nightly_notification_status_for_day task, which is responsible for clearing the counts for the current day in Redis

    1. Seed the Redis annual limit keys with notification counts and set the seeded_at key for 25 services
    2. On day 1, each service sends email and sms notifications, with 1 delivered and 1 failed.
    3. Call create_nightly_notification_status_for_day, marking the end of the day, clearing all counts for the services in Redis
    4. On day 2, send more notifications, and call process_sns_receipts to test seeding the counts for the day
    5. Pass some time and send more notifications, calling process_sns_receipts to test that counts increment post-seeding for the day
    """
    # Save notifications and seed annual limit notification data in redis for day 1
    services = []
    user_ids = []
    with freeze_time("2019-04-01T04:30"):
        # Use 25 services so we can test more than one processing chunk in create_nightly_notification_status_for_day
        for i in range(25):
            user = create_user(email=f"test{i}@test.ca", mobile_number=f"{i}234567890")
            service = create_service(service_id=uuid.uuid4(), service_name=f"service{i}", user=user, email_from=f"best.email{i}")
            template_sms = create_template(service=service)
            template_email = create_template(service=service, template_type="email")

            save_notification(create_notification(template_sms, status="delivered", created_at=datetime(2019, 4, 1, 4, 0)))
            save_notification(create_notification(template_email, status="delivered", created_at=datetime(2019, 4, 1, 4, 0)))
            save_notification(create_notification(template_sms, status="failed", created_at=datetime(2019, 4, 1, 4, 0)))
            save_notification(create_notification(template_email, status="failed", created_at=datetime(2019, 4, 1, 4, 0)))

            mapping = {"sms_failed": 1, "sms_delivered": 1, "email_failed": 1, "email_delivered": 1}
            annual_limit_client.set_seeded_at(service.id)
            annual_limit_client.seed_annual_limit_notifications(service.id, mapping)
            services.append(service)
            user_ids.append(user.id)

    # Run the nightly fact notification status task for the day to clear the annual limit counts and seeded_at key in redis
    with set_config(notify_api, "FF_ANNUAL_LIMIT", True):
        create_nightly_notification_status_for_day("2019-04-01")

        # Verify that all counts were cleared for all services and the seeded_at fields are set
        for service in services:
            assert all(value == 0 for value in annual_limit_client.get_all_notification_counts(service.id).values())
            assert annual_limit_client.get_annual_limit_status(service.id, "seeded_at") == "2019-04-01"

        # Moving onto day 2 - Testing the seeding process
        with freeze_time("2019-04-02T010:00"):
            # Insert delivered and failed notifications into the db so we can test that
            # the seeding process in process_sns_results collects notification counts correctly
            for service in services:
                template_email = next(template for template in service.templates if template.template_type == "email")
                template_sms = next(template for template in service.templates if template.template_type == "sms")

                save_notification(create_notification(template_sms, status="delivered", created_at=datetime(2019, 4, 2, 6, 0)))
                save_notification(create_notification(template_email, status="delivered", created_at=datetime(2019, 4, 2, 6, 0)))
                save_notification(create_notification(template_sms, status="failed", created_at=datetime(2019, 4, 2, 6, 0)))
                save_notification(create_notification(template_email, status="failed", created_at=datetime(2019, 4, 2, 6, 0)))

                save_notification(
                    create_notification(
                        template=template_sms,
                        reference=f"{service.name}-ref",
                        status="sent",
                        sent_by="sns",
                        sent_at=datetime.utcnow(),
                    )
                )
                # Invoke the process_sns_receipts task to re-seed the annual limit counts for the day in redis
                process_sns_results(sns_success_callback(reference=f"{service.name}-ref"))

                expected_counts = {
                    "sms_failed": 1,
                    "sms_delivered": 2,
                    "email_failed": 1,
                    "email_delivered": 1,
                }
                # Verify the counts are as expected and that seeded_at was set in redis
                assert annual_limit_client.get_all_notification_counts(service.id) == expected_counts
                assert annual_limit_client.was_seeded_today(service.id)

        # Day 2, some time passes - testing notification count increments when seeding has occurred
        with freeze_time("2019-04-02T14:00"):
            service = services[0]
            save_notification(
                create_notification(
                    template=next(template for template in service.templates if template.template_type == "sms"),
                    reference=f"{service.name}-ref1",
                    status="sent",
                    sent_by="sns",
                    sent_at=datetime.utcnow(),
                )
            )

            expected_counts = {
                "sms_failed": 1,
                "sms_delivered": 3,
                "email_failed": 1,
                "email_delivered": 1,
            }

            # Invoke process_sns_receipts, which should only increment sms_delivered as seeding has occurred for the day
            process_sns_results(sns_success_callback(reference=f"{service.name}-ref1"))  # Make sure the ref doesn't collide

            assert annual_limit_client.get_all_notification_counts(service.id) == expected_counts

            # Remove test data from redis
            annual_limit_client.delete_all_annual_limit_hashes([service.id for service in services])
