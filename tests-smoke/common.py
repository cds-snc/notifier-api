from boto3 import resource
import csv
from dotenv import load_dotenv
from io import StringIO
import itertools
import json
import uuid
import os
from notifications_utils.s3 import s3upload as utils_s3upload

load_dotenv()

CSV_UPLOAD_BUCKET_NAME = os.environ.get("CSV_UPLOAD_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_REGION")


def rows_to_csv(rows):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


def job_line(data, number_of_lines):
    return list(itertools.repeat([data, "test"], number_of_lines))


def pretty_print(data):
    print(json.dumps(data, indent=4, sort_keys=True))


# from admin app/s3_client/c3_csv_client.py ---------

FILE_LOCATION_STRUCTURE = "service-{}-notify/{}.csv"


def get_csv_location(service_id, upload_id):
    return (
        CSV_UPLOAD_BUCKET_NAME,
        FILE_LOCATION_STRUCTURE.format(service_id, upload_id),
    )


def s3upload(service_id, data):
    upload_id = str(uuid.uuid4())
    bucket_name, file_location = get_csv_location(service_id, upload_id)
    utils_s3upload(
        filedata=data,
        region=AWS_REGION,
        bucket_name=bucket_name,
        file_location=file_location,
    )
    return upload_id


def set_metadata_on_csv_upload(service_id, upload_id, **kwargs):
    get_csv_upload(service_id, upload_id).copy_from(
        CopySource="{}/{}".format(*get_csv_location(service_id, upload_id)),
        ServerSideEncryption="AES256",
        Metadata={key: str(value) for key, value in kwargs.items()},
        MetadataDirective="REPLACE",
    )


def get_csv_upload(service_id, upload_id):
    return get_s3_object(*get_csv_location(service_id, upload_id))


def get_s3_object(bucket_name, filename):
    s3 = resource("s3")
    return s3.Object(bucket_name, filename)


# --------------------------------------------------
