import json
from typing import Dict, List, Union
from urllib.parse import urljoin

import requests
from flask import current_app
from requests.auth import HTTPBasicAuth

from app.user.contact_request import ContactRequest

__all__ = ["Freshdesk"]


class Freshdesk(object):
    def __init__(self, contact: ContactRequest):
        self.contact = contact

    def _generate_description(self):
        message = self.contact.message
        if self.contact.is_demo_request():
            message = "<br><br>".join(
                [
                    f"- user: {self.contact.name} {self.contact.email_address}",
                    f"- department/org: {self.contact.department_org_name}",
                    f"- program/service: {self.contact.program_service_name}",
                    f"- intended recipients: {self.contact.intended_recipients}",
                    f"- main use case: {self.contact.main_use_case}",
                    f"- main use case details: {self.contact.main_use_case_details}",
                ]
            )
        elif self.contact.is_go_live_request():
            message = "<br>".join(
                [
                    f"{self.contact.service_name} just requested to go live.",
                    "",
                    f"- Department/org: {self.contact.department_org_name}",
                    f"- Intended recipients: {self.contact.intended_recipients}",
                    f"- Purpose: {self.contact.main_use_case}",
                    f"- Notification types: {self.contact.notification_types}",
                    f"- Expected monthly volume: {self.contact.expected_volume}",
                    "---",
                    self.contact.service_url,
                ]
            )
        elif self.contact.is_branding_request():
            message = "<br>".join(
                [
                    f"A new logo has been uploaded by {self.contact.name} ({self.contact.email_address}) for the following service:",
                    f"- Service id: {self.contact.service_id}",
                    f"- Service name: {self.contact.service_name}",
                    f"- Logo filename: {self.contact.branding_url}",
                    "<hr>",
                    f"Un nouveau logo a été téléchargé par {self.contact.name} ({self.contact.email_address}) pour le service suivant :",
                    f"- Identifiant du service : {self.contact.service_id}",
                    f"- Nom du service : {self.contact.service_name}",
                    f"- Nom du fichier du logo : {self.contact.branding_url}",
                ]
            )

        if len(self.contact.user_profile):
            message += f"<br><br>---<br><br> {self.contact.user_profile}"

        return message

    def _generate_ticket(self) -> Dict[str, Union[str, int, List[str]]]:
        product_id = current_app.config["FRESH_DESK_PRODUCT_ID"]
        if not product_id:
            raise NotImplementedError

        return {
            "product_id": int(product_id),
            "subject": self.contact.friendly_support_type,
            "description": self._generate_description(),
            "email": self.contact.email_address,
            "priority": 1,
            "status": 2,
            "tags": self.contact.tags,
        }

    def send_ticket(self) -> int:
        try:
            api_url = current_app.config["FRESH_DESK_API_URL"]
            if not api_url:
                raise NotImplementedError

            if current_app.config["FRESH_DESK_ENABLED"] is True:
                # The API and field definitions are defined here:
                # https://developer.zendesk.com/rest_api/docs/support/tickets
                response = requests.post(
                    urljoin(api_url, "/api/v2/tickets"),
                    json=self._generate_ticket(),
                    auth=HTTPBasicAuth(current_app.config["FRESH_DESK_API_KEY"], "x"),
                    timeout=5,
                )
                response.raise_for_status()

                return response.status_code
            else:
                return 201
        except requests.RequestException as e:
            content = json.loads(response.content)
            current_app.logger.error(f"Failed to create Freshdesk ticket: {content['errors']}")
            raise e
