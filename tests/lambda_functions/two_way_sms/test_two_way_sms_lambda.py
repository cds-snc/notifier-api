import pytest
import sys
from unittest import mock

from lambda_functions.two_way_sms.two_way_sms_lambda import two_way_sms_handler

VALID_TEST_RECIPIENT_PHONE_NUMBER = "+16502532222"


@pytest.mark.skip('WIP')
def test_two_way_sms_handler_with_sns(mocker):
    mock_boto = mock.Mock()
    sys.modules['boto3'] = mock_boto
    mock_sns = mocker.Mock()
    mock_boto.client.return_value = mock_sns

    mock_sns.opt_in_phone_number.return_value = {
        'ResponseMetadata': {
            'RequestId': 'request-id',
            'HTTPStatusCode': 200,
            'HTTPHeaders': {
                'date': 'Fri, 29 Jan 2021 22:05:47 GMT',
                'content-type': 'application/json',
                'content-length': '303',
                'connection': 'keep-alive',
                'x-amzn-requestid': 'request-id',
                'access-control-allow-origin': '*',
                'x-amz-apigw-id': 'other-id',
                'cache-control': 'no-store',
                'x-amzn-trace-id': 'trace-id'
            },
            'RetryAttempts': 0
        },
        'MessageResponse': {
            'ApplicationId': 'test-app-id',
            'RequestId': 'request-id',
            'Result': {
                VALID_TEST_RECIPIENT_PHONE_NUMBER: {
                    'DeliveryStatus': 'SUCCESSFUL',
                    'MessageId': 'test-message-id',
                    'StatusCode': 200,
                    'StatusMessage': 'MessageId: test-message-id'
                }
            }
        }
    }

    event = {
        "Records": [
            {
                "EventVersion": "1.0",
                "EventSubscriptionArn": "some_arn",
                "EventSource": "aws:sns",
                "Sns": {
                    "SignatureVersion": "1",
                    "Timestamp": "2019-01-02T12:45:07.000Z",
                    "Signature": "some signature",
                    "SigningCertUrl": "some_url",
                    "MessageId": "message-id",
                    "Message": '{\"originationNumber\":\"+16502532222\",'
                               '\"destinationNumber\":\"+from_number\",'
                               '\"messageKeyword\":\"keyword_blah\",'
                               '\"messageBody\":\"start\",'
                               '\"inboundMessageId\":\"inbound-message-id\",'
                               '\"previousPublishedMessageId\":\"prev-pub-msg-id\"}',
                    "MessageAttributes": {
                        "Test": {
                            "Type": "String",
                            "Value": "TestString"
                        },
                        "TestBinary": {
                            "Type": "Binary",
                            "Value": "TestBinary"
                        }
                    },
                    "Type": "Notification",
                    "UnsubscribeUrl": "some_url",
                    "TopicArn": "some-arn",
                    "Subject": "some-test-thing"
                }
            }
        ]
    }

    response = two_way_sms_handler(event, mocker.Mock())
    mock_sns.opt_in_phone_number.assert_called_once()

    assert response['StatusCode'] == 200
