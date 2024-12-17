from typing import Any, Dict

import requests

from .common import (
    Attachment_type,
    Config,
    Notification_type,
    pretty_print,
    single_succeeded,
)


def test_api_one_off(
    notification_type: Notification_type, attachment_type: Attachment_type = Attachment_type.NONE, local: bool = False
):
    if attachment_type is Attachment_type.NONE:
        print(f"test_api_oneoff ({notification_type.value})... ", end="", flush=True)
    else:
        print(f"test_api_oneoff ({notification_type.value}, file {attachment_type.value}ed)... ", end="", flush=True)

    if notification_type is Notification_type.SMS and attachment_type is not Attachment_type.NONE:
        print("Error: can't use files with sms")
        return

    data: Dict[str, Any]
    if notification_type == Notification_type.EMAIL:
        data = {
            "email_address": Config.EMAIL_TO,
            "template_id": Config.EMAIL_TEMPLATE_ID,
        }
    else:
        data = {
            "phone_number": Config.SMS_TO,
            "template_id": Config.SMS_TEMPLATE_ID,
        }
    if attachment_type is Attachment_type.ATTACHED:
        data["personalisation"] = {
            "var": "var",
            "application_file": {
                "file": "aGkgdGhlcmU=",
                "filename": "test_file.txt",
                "sending_method": f"{attachment_type.value}",
            },
        }
    elif attachment_type is Attachment_type.LINK:
        data["personalisation"] = {
            "var": {
                "file": "aGkgdGhlcmU=",
                "filename": "test_file.txt",
                "sending_method": f"{attachment_type.value}",
            }
        }
    else:
        data["personalisation"] = {
            "var": "smoke test api one off",
        }

    response = requests.post(
        f"{Config.API_HOST_NAME}/v2/notifications/{notification_type.value}",
        json=data,
        headers={"Authorization": f"ApiKey-v1 {Config.API_KEY}"},
    )
    if response.status_code != 201:
        pretty_print(response.json())
        print(f"FAILED: post to v2/notifications/{notification_type.value} failed")
        exit(1)

    if local:
        print(f"Check manually for 1 {notification_type.value}")
    else:
        uri = response.json()["uri"]
        success = single_succeeded(uri, use_jwt=False)
        if not success:
            print("FAILED: job didn't finish successfully")
            exit(1)
        print("Success")
