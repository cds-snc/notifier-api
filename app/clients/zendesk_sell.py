import json
import requests

from typing import Dict, List, Union, Optional
from urllib.parse import urljoin

from flask import current_app, escape

from app.authentication.bearer_auth import BearerAuth
from app.user.contact_request import ContactRequest
from app.models import Service, User

__all__ = [
    'ZenDeskSell'
]


class ZenDeskSell(object):

    def __init__(self):
        self.api_url = current_app.config['ZENDESK_SELL_API_URL']
        self.token = current_app.config['ZENDESK_SELL_API_KEY']

    @staticmethod
    def _name_split(name: str) -> (str, str):
        # FIXME: consider migrating to pypi/nameparser for proper name parsing to handle cases like:
        # 'Dr. Juan Q. Xavier de la Vega III (Doc Vega)'
        name_tokenised = name.split()
        return " ".join(name_tokenised[:-1]) if len(name_tokenised) > 1 else '', name_tokenised[-1]

    @staticmethod
    def _generate_lead_data(contact: ContactRequest) -> Dict[str, Union[str, List[str], Dict]]:

        # validation based upon api mandatory fields
        assert len(contact.name) or len(contact.department_org_name), 'Name or Org are mandatory field'

        recipients = {
            'internal': 'Colleagues within your department (internal)',
            'external': 'Partners from other organizations (external)',
            'public': 'Public'
        }

        first_name, last_name = ZenDeskSell._name_split(contact.name)
        return {
            'data': {
                'last_name': last_name,
                'first_name': first_name,
                'organization_name': contact.department_org_name,
                'email': contact.email_address,
                'description': f'Program: {contact.program_service_name}\n{contact.main_use_case}: '
                               f'{contact.main_use_case_details}',
                'tags': [contact.support_type, contact.language],
                'status': 'New',
                'source_id': 2085874,  # hard coded value defined by Zendesk for 'Demo request form'
                'custom_fields': {
                    'Product': ['Notify'],
                    'Intended recipients': recipients[contact.intended_recipients]
                    if contact.intended_recipients in recipients else 'No value'
                }
            }
        }

    @staticmethod
    def _generate_contact_data(user: User) -> Dict[str, Union[str, List[str], Dict]]:

        # validation based upon api mandatory fields
        assert len(user.name) and len(user.email_address), 'Name or email are mandatory field'

        first_name, last_name = ZenDeskSell._name_split(user.name)
        return {
            'data': {
                'last_name': last_name,
                'first_name': first_name,
                'email': user.email_address,
                'mobile': user.mobile_number,
            }
        }

    @staticmethod
    def _generate_deal_data(contact_id: int, service: Service, stage_id: int) -> Dict[str, Union[str, List[str], Dict]]:
        return {
            'data': {
                'contact_id': contact_id,
                'name': service.name,
                'stage_id': stage_id,
            }
        }

    def upsert_lead(self, contact: ContactRequest) -> int:

        if not self.api_url or not self.token:
            current_app.logger.warning('Did not upsert lead to zendesk')
            return 200

        # The API and field definitions are defined here: https://developers.getbase.com/docs/rest/reference/leads

        # name is mandatory for zen desk sell API
        assert len(contact.name), 'Zendesk sell requires a name for its API'

        try:
            response = requests.post(
                url=urljoin(self.api_url, f'/v2/leads/upsert?email={contact.email_address}'),
                data=json.dumps(ZenDeskSell._generate_lead_data(contact)),
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                auth=BearerAuth(token=self.token),
                timeout=5
            )
            response.raise_for_status()

            return response.status_code
        except requests.RequestException as e:
            content = json.loads(response.content)
            current_app.logger.warning(f"Failed to create zendesk sell lead: {content['errors']}")
            raise e

    def upsert_contact(self, user: User) -> (Optional[int], bool):

        # The API and field definitions are defined here: https://developers.getbase.com/docs/rest/reference/contacts
        if not self.api_url or not self.token:
            current_app.logger.warning('Did not upsert contact to zendesk')
            return None, False

        try:
            response = requests.post(
                url=urljoin(self.api_url, f'/v2/contacts/upsert?email={user.email_address}'),
                data=json.dumps(ZenDeskSell._generate_contact_data(user)),
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                auth=BearerAuth(token=self.token),
                timeout=5
            )
            response.raise_for_status()
        except requests.RequestException:
            current_app.logger.warning('Failed to create zendesk sell contact')
            return None, False

        # response validation
        try:
            resp_data = json.loads(response.text)
            assert 'data' in resp_data, 'Missing "data" field in response'
            assert 'id' in resp_data['data'], 'Missing "id" field in response'
            assert 'created_at' in resp_data['data'], 'Missing "created_at" field in response'
            assert 'updated_at' in resp_data['data'], 'Missing "updated_at" field in response'

            return resp_data['data']['id'], resp_data['data']['created_at'] == resp_data['data']['updated_at']

        except (json.JSONDecodeError, AssertionError):
            current_app.logger.warning(f'Invalid response: {response.text}')
            return None, False

    def delete_contact(self, contact_id: int) -> None:

        if not self.api_url or not self.token:
            current_app.logger.warning(f'Did not delete contact[{contact_id}] from zendesk')
            return

        # The API and field definitions are defined here: https://developers.getbase.com/docs/rest/reference/contacts
        try:
            response = requests.delete(
                url=urljoin(self.api_url, f'/v2/contacts/{contact_id}'),
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                auth=BearerAuth(token=self.token),
                timeout=5
            )
            response.raise_for_status()
        except requests.RequestException:
            current_app.logger.warning(f'Failed to delete zendesk sell contact: {contact_id}')

    def upsert_deal(self, contact_id: int, service: Service, stage_id: int) -> Optional[int]:
        # The API and field definitions are defined here: https://developers.getbase.com/docs/rest/reference/deals
        if not self.api_url or not self.token:
            current_app.logger.warning('Did not upsert deal to zendesk')
            return None

        try:
            response = requests.post(
                url=urljoin(self.api_url, f'/v2/deals/upsert?contact_id={contact_id}&name={escape(service.name)}'),
                data=json.dumps(ZenDeskSell._generate_deal_data(contact_id, service, stage_id)),
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
                auth=BearerAuth(token=self.token),
                timeout=5
            )
            response.raise_for_status()
        except requests.RequestException:
            current_app.logger.warning('Failed to create zendesk sell deal')
            return None

        # response validation
        try:
            resp_data = json.loads(response.text)
            assert 'data' in resp_data, 'Missing "data" field in response'
            assert 'id' in resp_data['data'], 'Missing "id" field in response'
            assert 'contact_id' in resp_data['data'], 'Missing "contact_id" field in response'

            return resp_data['data']['id']

        except (json.JSONDecodeError, AssertionError):
            current_app.logger.warning(f'Invalid response: {response.text}')
            return None

    def send_create_service(self, service: Service, user: User) -> bool:
        # Upsert a contact (create/update). Only when this is successful does the software upsert a deal
        # and link the deal to the contact.
        # If upsert deal fails go back and delete the contact ONLY if it never existed before
        contact_id, is_created = self.upsert_contact(user)
        if not contact_id:
            return False

        # 11826762 is a zendesk number to signify "Created Trial"
        deal_id = self.upsert_deal(contact_id, service, 11826762)
        if not deal_id and is_created:
            # best effort here
            self.upsert_contact(contact_id)
            return False

        return True

    def send_contact_request(self, contact: ContactRequest) -> int:
        ret = 200
        if contact.is_demo_request():
            ret = self.upsert_lead(contact)

        return ret
