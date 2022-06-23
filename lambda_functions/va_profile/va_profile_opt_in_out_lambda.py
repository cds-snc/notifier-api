"""
Tutorial: Configuring a Lambda function to access Amazon RDS in an Amazon VPC
   https://docs.aws.amazon.com/lambda/latest/dg/services-rds-tutorial.html

https://www.psycopg.org/docs/usage.html

This code makes use of module variables (db_connection & PEM) for runtime efficiency.
"""

import boto3
import logging
import os
import psycopg2
import sys
from http.client import HTTPSConnection
from json import dumps
from ssl import SSLContext, SSLError


OPT_IN_OUT_QUERY = """SELECT va_profile_opt_in_out(%s, %s, %s, %s, %s);"""
NOTIFY_ENVIRONMENT = os.getenv("NOTIFY_ENVIRONMENT")
PEM = None
SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
VA_PROFILE_DOMAIN = os.getenv("VA_PROFILE_DOMAIN")
VA_PROFILE_PATH_BASE = "/communication-hub/communication/v1/status/changelog/"


if SQLALCHEMY_DATABASE_URI is None:
    logging.error("The database URI is not set.")
    sys.exit("Couldn't connect to the database.")


def get_pem():
    """
    Read a .pem file from AWS Parameter Store.

    AWS_REGION is set during unit testing.  In a deployment environment,
    region_name=None will result in boto using the "default session".
      https://boto3.amazonaws.com/v1/documentation/api/latest/reference/core/boto3.html?highlight=boto3.client#boto3.client
    """

    ssm_client = boto3.client("ssm", region_name=os.getenv("AWS_REGION"))

    response = ssm_client.get_parameter(
        Name=f"/{NOTIFY_ENVIRONMENT}/notification-api/profile-integration-pem",
        WithDecryption=True
    )

    pem = response.get("Parameter", {}).get("Value", None)

    if pem is None:
        logging.debug(response)

    return pem


PEM = None


if NOTIFY_ENVIRONMENT is None:
    logging.error("Couldn't get the Notify environment.  This is necessary to retrieve the .pem file.")
elif NOTIFY_ENVIRONMENT != "test":
    PEM = get_pem()

    if PEM is None:
        logging.error("Couldn't get the .pem chain from SSM.")


if VA_PROFILE_DOMAIN is None:
    logging.error("Could not get the domain for VA Profile.")


def make_connection(worker_id):
    """
    Return a connection to the database, or return None.

    https://www.psycopg.org/docs/module.html#psycopg2.connect
    https://www.psycopg.org/docs/module.html#exceptions
    """

    connection = None

    try:
        connection = psycopg2.connect(SQLALCHEMY_DATABASE_URI + ('' if worker_id is None else f"_{worker_id}"))
    except psycopg2.Warning as e:
        logging.warning(e)
    except psycopg2.Error as e:
        logging.exception(e)
        logging.error(e.pgcode)

    return connection


db_connection = None


def va_profile_opt_in_out_lambda_handler(event: dict, context, worker_id=None) -> dict:
    """
    Use the event data to process veterans' opt-in/out requests as relayed by VA Profile.  The data fields
    are as specified in the "VA Profile Syncronization" document.  It looks like this:

        {
            txAuditId": "string",
            ...
            "bios": [
                {
                    "txAuditId": "string",
                    "sourceDate": "2022-03-07T19:37:59.320Z",
                    "vaProfileId": 0,
                    "communicationChannelId": 0,
                    "communicationItemId": 0,
                    "allowed": true,
                    ...
                }
            ]
        }

    The lambda generally will be called by the Amazon API Gateway service.  Useful documentation:
        https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway.html
        https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html
        https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-concepts.html#gettingstarted-concepts-event

    When this function is called from a unit test, the database URI will differ slightly from the environment
    variable, SQLALCHEMY_DATABASE_URI.  The parameter worker_id is used to construct the modified name in the
    same manner as in the tests/conftest.py::notify_db fixture.
    """

    global db_connection, pem
    logging.debug(event)

    if "txAuditId" not in event or "bios" not in event or not isinstance(event["bios"], list):
        return { "statusCode": 400, "body": "A required top level attribute is missing from the request or has the wrong type." }

    response = { "statusCode": 200 }

    put_request_body = {
        "txAuditId": event["txAuditId"],
        "bios": [],
    }

    # Process the preference updates.
    for record in event["bios"]:
        put_record = {
            "vaProfileId": record.get("vaProfileId", "unknown"),
            "communicationChannelId": record.get("communicationChannelId", "unknown"),
            "communicationItemId": record.get("communicationItemId", "unknown"),
        }

        if record.get("txAuditId", '') != event["txAuditId"]:
            # Do not query the database in response to this record.
            put_record["status"] = "COMPLETED_FAILURE"
            put_record["info"] = "The record's txAuditId, {}, does not match the event's txAuditId, {}.".format(record.get('txAuditId', '<unknown>'), event["txAuditId"])
            put_request_body["bios"].append(put_record)
            continue

        try:
            params = (                             # Stored function parameters:
                record["vaProfileId"],             #     _va_profile_id
                record["communicationItemId"],     #     _communication_item_id
                record["communicationChannelId"],  #     _communication_channel_name
                record["allowed"],                 #     _allowed
                record["sourceDate"],              #     _source_datetime
            )

            if db_connection is None or db_connection.status != 0:
                # Attempt to (re-)establish a database connection
                db_connection = make_connection(worker_id)

            if db_connection is None:
                raise RuntimeError("No database connection.")

            # Execute the stored function.
            with db_connection.cursor() as c:
                # https://www.psycopg.org/docs/cursor.html#cursor.execute
                c.execute(OPT_IN_OUT_QUERY, params)
                put_record["status"] = "COMPLETED_SUCCESS" if c.fetchone()[0] else "COMPLETED_NOOP"
                db_connection.commit()
        except KeyError as e:
            # Bad Request.  Required attributes are missing.
            response["statusCode"] = 400
            put_record["status"] = "COMPLETED_FAILURE"
            logging.exception(e)
        except Exception as e:
            # Internal Server Error.  Prefer to return 400 if multiple records raise exceptions.
            if response["statusCode"] != 400:
                response["statusCode"] = 500
            put_record["status"] = "COMPLETED_FAILURE"
            logging.exception(e)
        finally:
            put_request_body["bios"].append(put_record)

    if len(put_request_body["bios"]) > 0 and PEM is not None and VA_PROFILE_DOMAIN is not None:
        context = SSLContext()

        try:
            # Load the PEM contents for the certificate used for VA Notify.  This is necessary for 2-way TLS.
            context.load_pem_chain(PEM)

            try:
                # Make a PUT request to VA Profile.
                https_connection = HTTPSConnection(VA_PROFILE_DOMAIN, context=context)
                https_connection.request(
                    "PUT",
                    VA_PROFILE_PATH_BASE + event["txAuditId"],
                    dumps(put_request_body),
                    { "Content-Type": "application/json" }
                )
                put_response = https_connection.response()
                if put_response.status != 200:
                    logging.info("VA Profile responded with HTTP status %d.", put_response.status)
                    logging.debug(put_response)
            except ConnectionError as e:
                logging.error("The PUT request to VA Profile failed.")
                logging.exception(e)
            finally:
                https_connection.close()
        except SSLError as e:
            logging.exception(e)

    return response
