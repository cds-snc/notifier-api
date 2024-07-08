import pytest

from app import aws_pinpoint_client
from tests.conftest import set_config_values


@pytest.mark.serial
def test_send_sms_sends_to_default_pool(notify_api, mocker, sample_template):
    boto_mock = mocker.patch.object(aws_pinpoint_client, "_client", create=True)
    mocker.patch.object(aws_pinpoint_client, "statsd_client", create=True)
    to = "6135555555"
    content = "foo"
    reference = "ref"

    with set_config_values(
        notify_api,
        {
            "AWS_PINPOINT_SC_POOL_ID": "sc_pool_id",
            "AWS_PINPOINT_DEFAULT_POOL_ID": "default_pool_id",
            "AWS_PINPOINT_CONFIGURATION_SET_NAME": "config_set_name",
            "AWS_PINPOINT_SC_TEMPLATE_IDS": [],
        },
    ):
        aws_pinpoint_client.send_sms(to, content, reference=reference, template_id=sample_template.id)

    boto_mock.send_text_message.assert_called_once_with(
        DestinationPhoneNumber="+16135555555",
        OriginationIdentity="default_pool_id",
        MessageBody=content,
        MessageType="TRANSACTIONAL",
        ConfigurationSetName="config_set_name",
    )


@pytest.mark.serial
def test_send_sms_sends_to_shortcode_pool(notify_api, mocker, sample_template):
    boto_mock = mocker.patch.object(aws_pinpoint_client, "_client", create=True)
    mocker.patch.object(aws_pinpoint_client, "statsd_client", create=True)
    to = "6135555555"
    content = "foo"
    reference = "ref"

    with set_config_values(
        notify_api,
        {
            "AWS_PINPOINT_SC_POOL_ID": "sc_pool_id",
            "AWS_PINPOINT_DEFAULT_POOL_ID": "default_pool_id",
            "AWS_PINPOINT_CONFIGURATION_SET_NAME": "config_set_name",
            "AWS_PINPOINT_SC_TEMPLATE_IDS": [str(sample_template.id)],
        },
    ):
        aws_pinpoint_client.send_sms(to, content, reference=reference, template_id=sample_template.id)

    boto_mock.send_text_message.assert_called_once_with(
        DestinationPhoneNumber="+16135555555",
        OriginationIdentity="sc_pool_id",
        MessageBody=content,
        MessageType="TRANSACTIONAL",
        ConfigurationSetName="config_set_name",
    )


def test_send_sms_returns_raises_error_if_there_is_no_valid_number_is_found(notify_api, mocker):
    mocker.patch.object(aws_pinpoint_client, "_client", create=True)
    mocker.patch.object(aws_pinpoint_client, "statsd_client", create=True)

    to = ""
    content = reference = "foo"

    with pytest.raises(ValueError) as excinfo:
        aws_pinpoint_client.send_sms(to, content, reference)

    assert "No valid numbers found for SMS delivery" in str(excinfo.value)


def test_handles_opted_out_numbers(notify_api, mocker, sample_template):
    # mock_client = mocker.patch.object(aws_pinpoint_client, "_client", create=True)
    # mocker.patch.object(aws_pinpoint_client, "statsd_client", create=True)

    conflict_error = aws_pinpoint_client._client.exceptions.ConflictException(
        error_response={"Reason": "DESTINATION_PHONE_NUMBER_OPTED_OUT"}, operation_name="send_text_message"
    )
    mocker.patch("app.aws_pinpoint_client._client.send_text_message", side_effect=conflict_error)

    to = "6135555555"
    content = "foo"
    reference = "ref"
    assert aws_pinpoint_client.send_sms(to, content, reference=reference, template_id=sample_template.id) == "opted_out"
