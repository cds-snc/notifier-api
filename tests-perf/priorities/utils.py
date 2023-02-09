import csv
from datetime import datetime
from io import StringIO
from typing import Iterator, List, Optional


def api_headers(api_key: str):
    return {"Authorization": f"ApiKey-v1 {api_key[-36:]}"}


def json_data(email_address: str, template_id: str, ref: str, personalisation: Optional[dict] = {}):
    return {
        "reference": f"{datetime.utcnow().isoformat()} {ref}",
        "email_address": email_address,
        "template_id": template_id,
        "personalisation": personalisation,
    }


def json_data_sms(phone_number: str, template_id: str, ref: str, personalisation: Optional[dict] = {}):
    return {
        "reference": f"{datetime.utcnow().isoformat()} {ref}",
        "phone_number": phone_number,
        "template_id": template_id,
        "personalisation": personalisation,
    }


def rows_to_csv(rows: List[List[str]]):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


def job_line(data: str, number_of_lines: int) -> Iterator[List[str]]:
    return map(lambda n: [data, f"var{n}"], range(0, number_of_lines))
