import argparse

from smoke.common import Attachment_type, Config, Notification_type  # type: ignore
from smoke.test_admin_csv import test_admin_csv  # type: ignore
from smoke.test_admin_one_off import test_admin_one_off  # type: ignore
from smoke.test_api_bulk import test_api_bulk  # type: ignore
from smoke.test_api_one_off import test_api_one_off  # type: ignore

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-l", "--local", default=False, action="store_true", help="run locally, do not check for delivery success (default false)"
    )
    parser.add_argument("--nofiles", default=False, action="store_true", help="do not send files (default false)")
    args = parser.parse_args()

    print("API Smoke test\n")
    for key in ["API_HOST_NAME", "SERVICE_ID", "EMAIL_TEMPLATE_ID", "SMS_TEMPLATE_ID", "EMAIL_TO", "SMS_TO"]:
        print(f"{key:>17}: {Config.__dict__[key]}")
    print("")

    for notification_type in [Notification_type.EMAIL, Notification_type.SMS]:
        test_admin_one_off(notification_type, local=args.local)
        test_admin_csv(notification_type, local=args.local)
        test_api_one_off(notification_type, local=args.local)
        test_api_bulk(notification_type, local=args.local)

    if not args.nofiles:
        test_api_one_off(Notification_type.EMAIL, attachment_type=Attachment_type.ATTACHED, local=args.local)
        test_api_one_off(Notification_type.EMAIL, attachment_type=Attachment_type.LINK, local=args.local)
