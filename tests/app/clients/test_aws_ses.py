import re
from base64 import b64encode
from textwrap import dedent

import botocore
import pytest
from pytest_mock import MockerFixture
from notifications_utils.recipients import InvalidEmailError

from app import aws_ses_client
from app.clients.email.aws_ses import AwsSesClientException, punycode_encode_email


def email_b64_encoding(input):
    return f"=?utf-8?b?{b64encode(input.encode('utf-8')).decode('utf-8')}?="


@pytest.mark.parametrize(
    "reply_to_address, expected_value",
    [
        (None, []),
        ("foo@bar.com", "foo@bar.com"),
        (
            "føøøø@bååååår.com",
            email_b64_encoding(punycode_encode_email("føøøø@bååååår.com")),
        ),
    ],
    ids=["empty", "single_email", "punycode"],
)
def test_send_email_handles_reply_to_address(notify_api, mocker, reply_to_address, expected_value):
    boto_mock = mocker.patch.object(aws_ses_client, "_client", create=True)
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)

    with notify_api.app_context():
        aws_ses_client.send_email(
            source="from@address.com",
            to_addresses="to@address.com",
            subject="Subject",
            body="Body",
            reply_to_address=reply_to_address,
        )

    boto_mock.send_raw_email.assert_called()
    raw_message = boto_mock.send_raw_email.call_args.kwargs["RawMessage"]["Data"]
    if not expected_value:
        assert "reply-to" not in raw_message
    else:
        assert f"reply-to: {expected_value}" in raw_message


def test_send_email_txt_and_html_email(notify_api, mocker):
    boto_mock = mocker.patch.object(aws_ses_client, "_client", create=True)
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)

    with notify_api.app_context():
        aws_ses_client.send_email(
            "from@example.com",
            to_addresses="destination@example.com",
            subject="Subject",
            body="email body",
            html_body="<p>email body</p>",
            reply_to_address="reply@example.com",
        )

    boto_mock.send_raw_email.assert_called_once()
    raw_message = boto_mock.send_raw_email.call_args.kwargs["RawMessage"]["Data"]

    regex = dedent(
        r"""
        Content-Type: multipart\/alternative; boundary="===============(?P<boundary>.+)=="
        MIME-Version: 1\.0
        Subject: Subject
        From: from@example\.com
        To: destination@example\.com
        reply-to: reply@example\.com

        --===============(?P<b1>.+)==
        Content-Type: text/plain; charset="us-ascii"
        MIME-Version: 1\.0
        Content-Transfer-Encoding: 7bit

        email body
        --===============(?P<b2>.+)==
        Content-Type: text/html; charset="us-ascii"
        MIME-Version: 1\.0
        Content-Transfer-Encoding: 7bit

        <p>email body</p>
        --===============(?P<b3>.+)==--
    """
    ).strip()

    assert len(set(re.findall(regex, raw_message))) == 1
    assert re.match(regex, raw_message)


def test_send_email_txt_and_html_email_with_attachment(notify_api, mocker):
    boto_mock = mocker.patch.object(aws_ses_client, "_client", create=True)
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)

    with notify_api.app_context():
        aws_ses_client.send_email(
            "from@example.com",
            to_addresses="destination@example.com",
            subject="Subject",
            body="email body",
            html_body="<p>email body</p>",
            attachments=[{"data": "Canada", "name": "file.txt", "mime_type": "text/plain"}],
            reply_to_address="reply@example.com",
        )

    boto_mock.send_raw_email.assert_called_once()
    raw_message = boto_mock.send_raw_email.call_args.kwargs["RawMessage"]["Data"]

    regex = dedent(
        r"""
        Content-Type: multipart/mixed; boundary="===============(?P<boundary>.+)=="
        MIME-Version: 1\.0
        Subject: Subject
        From: from@example\.com
        To: destination@example\.com
        reply-to: reply@example\.com

        --===============(?P<b1>.+)==
        Content-Type: multipart/alternative; boundary="===============(?P<b2>.+)=="
        MIME-Version: 1\.0

        --===============(?P<b3>.+)==
        Content-Type: text/plain; charset="us-ascii"
        MIME-Version: 1\.0
        Content-Transfer-Encoding: 7bit

        email body
        --===============(?P<b4>.+)==
        Content-Type: text/html; charset="us-ascii"
        MIME-Version: 1\.0
        Content-Transfer-Encoding: 7bit

        <p>email body</p>
        --===============(?P<b5>.+)==--

        --===============(?P<b6>.+)==
        Content-Type: application/octet-stream
        MIME-Version: 1\.0
        Content-Transfer-Encoding: base64
        Content-Type: text/plain; name="file\.txt"
        Content-Disposition: attachment; filename="file\.txt"

        Q2FuYWRh

        --===============(?P<b7>.+)==--
    """
    ).strip()

    groups = re.match(regex, raw_message).groupdict()
    assert groups["boundary"] == groups["b7"] == groups["b6"] == groups["b1"]
    assert groups["b2"] == groups["b3"] == groups["b4"] == groups["b5"]
    assert re.match(regex, raw_message)


def test_send_email_handles_punycode_to_address(notify_api, mocker):
    boto_mock = mocker.patch.object(aws_ses_client, "_client", create=True)
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)

    with notify_api.app_context():
        aws_ses_client.send_email(
            "from@address.com",
            to_addresses="føøøø@bååååår.com",
            subject="Subject",
            body="Body",
        )

    boto_mock.send_raw_email.assert_called()
    raw_message = boto_mock.send_raw_email.call_args.kwargs["RawMessage"]["Data"]
    expected_to = email_b64_encoding(punycode_encode_email("føøøø@bååååår.com"))
    assert f"To: {expected_to}" in raw_message


def test_send_email_raises_bad_email_as_InvalidEmailError(mocker: MockerFixture):
    boto_mock = mocker.patch.object(aws_ses_client, "_client", create=True)
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)
    error_response = {
        "Error": {
            "Code": "InvalidParameterValue",
            "Message": "some error message from amazon",
            "Type": "Sender",
        }
    }
    boto_mock.send_raw_email.side_effect = botocore.exceptions.ClientError(error_response, "opname")
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)

    with pytest.raises(InvalidEmailError) as excinfo:
        aws_ses_client.send_email(
            source="from@address.com",
            to_addresses="definitely@invalid_email.com",
            subject="Subject",
            body="Body",
        )

    assert "some error message from amazon" in str(excinfo.value)
    assert "definitely@invalid_email.com" in str(excinfo.value)


def test_send_email_raises_other_errs_as_AwsSesClientException(mocker: MockerFixture):
    boto_mock = mocker.patch.object(aws_ses_client, "_client", create=True)
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)
    error_response = {
        "Error": {
            "Code": "ServiceUnavailable",
            "Message": "some error message from amazon",
            "Type": "Sender",
        }
    }
    boto_mock.send_raw_email.side_effect = botocore.exceptions.ClientError(error_response, "opname")
    mocker.patch.object(aws_ses_client, "statsd_client", create=True)

    with pytest.raises(AwsSesClientException) as excinfo:
        aws_ses_client.send_email(
            source="from@address.com",
            to_addresses="foo@bar.com",
            subject="Subject",
            body="Body",
        )

    assert "some error message from amazon" in str(excinfo.value)


@pytest.mark.parametrize(
    "input, expected_output",
    [
        ("foo@domain.tld", "foo@domain.tld"),
        ("føøøø@bååååår.com", "føøøø@xn--br-yiaaaaa.com"),
    ],
)
def test_punycode_encode_email(input, expected_output):
    assert punycode_encode_email(input) == expected_output
