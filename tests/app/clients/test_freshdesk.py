import base64
from typing import Any, Dict

import pytest
import requests_mock
from flask import Flask
from requests import RequestException

from app.clients import freshdesk
from app.user.contact_request import ContactRequest
from tests.conftest import set_config_values


class TestSendTicket:
    @pytest.fixture()
    def email_freshdesk_ticket_mock(self, mocker):
        temp = freshdesk.Freshdesk.email_freshdesk_ticket
        freshdesk.Freshdesk.email_freshdesk_ticket = mocker.Mock()
        yield freshdesk.Freshdesk.email_freshdesk_ticket
        freshdesk.Freshdesk.email_freshdesk_ticket = temp

    def test_send_ticket_demo(self, email_freshdesk_ticket_mock, notify_api: Flask):
        def match_json(request):
            expected = {
                "product_id": 42,
                "subject": "friendly-support-type-test",
                "description": "- user: name-test test@email.com<br><br>"
                "- department/org: dept-test<br><br>"
                "- program/service: service-test<br><br>"
                "- intended recipients: internal<br><br>"
                "- main use case: main-use-case-test<br><br>"
                "- main use case details: main-use-case-details-test",
                "email": "test@email.com",
                "priority": 1,
                "status": 2,
                "tags": [],
            }

            encoded_auth = base64.b64encode(b"freshdesk-api-key:x").decode("ascii")
            json_matches = request.json() == expected
            basic_auth_header = request.headers.get("Authorization") == f"Basic {encoded_auth}"

            return json_matches and basic_auth_header

        with requests_mock.mock() as rmock:
            rmock.request(
                "POST",
                "https://freshdesk-test.com/api/v2/tickets",
                additional_matcher=match_json,
                status_code=201,
            )

            contact_request: Dict[str, Any] = {
                "email_address": "test@email.com",
                "name": "name-test",
                "department_org_name": "dept-test",
                "program_service_name": "service-test",
                "intended_recipients": "internal",
                "main_use_case": "main-use-case-test",
                "main_use_case_details": "main-use-case-details-test",
                "friendly_support_type": "friendly-support-type-test",
                "language": "en",
                "support_type": "demo",
            }

            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(**contact_request)).send_ticket()
                assert response == 201
                email_freshdesk_ticket_mock.assert_not_called()

    def test_send_ticket_go_live_request(self, email_freshdesk_ticket_mock, notify_api: Flask):
        def match_json(request):
            expected = {
                "product_id": 42,
                "subject": "Support Request",
                "description": "t6 just requested to go live.<br>"
                "<br>"
                "- Department/org: department_org_name<br>"
                "- Intended recipients: internal, external, public<br>"
                "- Purpose: main_use_case<br>"
                "<br>"
                "- Expected email volumes:<br>"
                "- Daily: above_limit (None)<br>"
                "- Yearly: within_limit<br>"
                "<br>"
                "- Expected SMS volumes:<br>"
                "- Daily: more_sms (54321)<br>"
                "- Yearly: above_limit<br>"
                "---<br>"
                "http://localhost:6012/services/8624bd36-b70b-4d4b-a459-13e1f4770b92",
                "email": "test@email.com",
                "priority": 1,
                "status": 2,
                "tags": [],
            }

            encoded_auth = base64.b64encode(b"freshdesk-api-key:x").decode("ascii")
            json_matches = request.json() == expected
            basic_auth_header = request.headers.get("Authorization") == f"Basic {encoded_auth}"

            return json_matches and basic_auth_header

        with requests_mock.mock() as rmock:
            rmock.request(
                "POST",
                "https://freshdesk-test.com/api/v2/tickets",
                additional_matcher=match_json,
                status_code=201,
            )
            data: Dict[str, Any] = {
                "email_address": "test@email.com",
                "name": "name",
                "department_org_name": "department_org_name",
                "intended_recipients": "internal, external, public",
                "main_use_case": "main_use_case",
                "friendly_support_type": "Support Request",
                "support_type": "go_live_request",
                "service_name": "t6",
                "service_id": "8624bd36-b70b-4d4b-a459-13e1f4770b92",
                "service_url": "http://localhost:6012/services/8624bd36-b70b-4d4b-a459-13e1f4770b92",
                "notification_types": "email, sms",
                "expected_volume": "100k+",
                "daily_email_volume": "above_limit",
                "annual_email_volume": "within_limit",
                "daily_sms_volume": "more_sms",
                "annual_sms_volume": "above_limit",
                "how_many_more_email": None,
                "how_many_more_sms": 54321,
            }
            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(**data)).send_ticket()
                assert response == 201
                email_freshdesk_ticket_mock.assert_not_called()

    def test_send_ticket_branding_request(self, email_freshdesk_ticket_mock, notify_api: Flask):
        def match_json(request):
            expected = {
                "product_id": 42,
                "subject": "Branding request",
                "description": "A new logo has been uploaded by name (test@email.com) for the following service:<br>"
                "- Service id: 8624bd36-b70b-4d4b-a459-13e1f4770b92<br>"
                "- Service name: t6<br>"
                "- Organisation id: 6b72e84f-8591-42e1-93b8-7d24a45e1d79<br>"
                "- Organisation name: best org name ever<br>"
                "- Logo filename: branding_url<br>"
                "- Logo name: branding_logo_name<br>"
                "- Alt text english: en alt text<br>"
                "- Alt text french: fr alt text<br>"
                "<hr><br>"
                "Un nouveau logo a été téléchargé par name (test@email.com) pour le service suivant :<br>"
                "- Identifiant du service : 8624bd36-b70b-4d4b-a459-13e1f4770b92<br>"
                "- Nom du service : t6<br>"
                "- Identifiant de l'organisation: 6b72e84f-8591-42e1-93b8-7d24a45e1d79<br>"
                "- Nom de l'organisation: best org name ever<br>"
                "- Nom du fichier du logo : branding_url<br>"
                "- Nom du logo : branding_logo_name<br>"
                "- Texte alternatif anglais : en alt text<br>"
                "- Texte alternatif français : fr alt text",
                "email": "test@email.com",
                "priority": 1,
                "status": 2,
                "tags": [],
            }

            encoded_auth = base64.b64encode(b"freshdesk-api-key:x").decode("ascii")
            json_matches = request.json() == expected
            basic_auth_header = request.headers.get("Authorization") == f"Basic {encoded_auth}"

            return json_matches and basic_auth_header

        with requests_mock.mock() as rmock:
            rmock.request(
                "POST",
                "https://freshdesk-test.com/api/v2/tickets",
                additional_matcher=match_json,
                status_code=201,
            )
            data: Dict[str, Any] = {
                "email_address": "test@email.com",
                "name": "name",
                "friendly_support_type": "Branding request",
                "support_type": "branding_request",
                "service_name": "t6",
                "organisation_id": "6b72e84f-8591-42e1-93b8-7d24a45e1d79",
                "department_org_name": "best org name ever",
                "service_id": "8624bd36-b70b-4d4b-a459-13e1f4770b92",
                "branding_url": "branding_url",
                "branding_logo_name": "branding_logo_name",
                "alt_text_en": "en alt text",
                "alt_text_fr": "fr alt text",
            }
            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(**data)).send_ticket()
                assert response == 201
                email_freshdesk_ticket_mock.assert_not_called()

    def test_send_ticket_other_category(self, email_freshdesk_ticket_mock, notify_api: Flask):
        def match_json(request):
            expected = {
                "product_id": 42,
                "subject": "New template category request",
                "description": "New template category request from name (test@email.com):<br>"
                "- Service id: 8624bd36-b70b-4d4b-a459-13e1f4770b92<br>"
                "- New Template Category Request name: test category name<br>"
                "- Template id request: http://localhost:6012/services/8624bd36-b70b-4d4b-a459-13e1f4770b92/templates/3ed1f07a-1b20-4f83-9a3e-158ab9b00103<br>"
                "<hr><br>"
                "Demande de nouvelle catégorie de modèle de name (test@email.com):<br>"
                "- Identifiant du service: 8624bd36-b70b-4d4b-a459-13e1f4770b92<br>"
                "- Nom de la nouvelle catégorie de modèle demandée: test category name<br>"
                "- Demande d'identifiant de modèle: http://localhost:6012/services/8624bd36-b70b-4d4b-a459-13e1f4770b92/templates/3ed1f07a-1b20-4f83-9a3e-158ab9b00103",
                "email": "test@email.com",
                "priority": 1,
                "status": 2,
                "tags": [],
            }

            encoded_auth = base64.b64encode(b"freshdesk-api-key:x").decode("ascii")
            json_matches = request.json() == expected
            basic_auth_header = request.headers.get("Authorization") == f"Basic {encoded_auth}"

            return json_matches and basic_auth_header

        with requests_mock.mock() as rmock:
            rmock.request(
                "POST",
                "https://freshdesk-test.com/api/v2/tickets",
                additional_matcher=match_json,
                status_code=201,
            )
            data: Dict[str, Any] = {
                "email_address": "test@email.com",
                "name": "name",
                "friendly_support_type": "New template category request",
                "support_type": "new_template_category_request",
                "service_id": "8624bd36-b70b-4d4b-a459-13e1f4770b92",
                "template_category_name_en": "test category name",
                "template_category_name_fr": "test category name",
                "template_id_link": "http://localhost:6012/services/8624bd36-b70b-4d4b-a459-13e1f4770b92/templates/3ed1f07a-1b20-4f83-9a3e-158ab9b00103",
            }
            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(**data)).send_ticket()
                assert response == 201
                email_freshdesk_ticket_mock.assert_not_called()

    def test_send_ticket_other(self, email_freshdesk_ticket_mock, notify_api: Flask):
        def match_json(request):
            expected = {
                "product_id": 42,
                "subject": "Support Request",
                "description": "",
                "email": "test@email.com",
                "priority": 1,
                "status": 2,
                "tags": [],
            }

            encoded_auth = base64.b64encode(b"freshdesk-api-key:x").decode("ascii")
            json_matches = request.json() == expected
            basic_auth_header = request.headers.get("Authorization") == f"Basic {encoded_auth}"

            return json_matches and basic_auth_header

        with requests_mock.mock() as rmock:
            rmock.request(
                "POST",
                "https://freshdesk-test.com/api/v2/tickets",
                additional_matcher=match_json,
                status_code=201,
            )

            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(email_address="test@email.com")).send_ticket()
                assert response == 201
                email_freshdesk_ticket_mock.assert_not_called()

    def test_send_ticket_user_profile(self, email_freshdesk_ticket_mock, notify_api: Flask):
        def match_json(request):
            expected = {
                "product_id": 42,
                "subject": "Support Request",
                "description": "<br><br>---<br><br> user_profile",
                "email": "test@email.com",
                "priority": 1,
                "status": 2,
                "tags": [],
            }

            encoded_auth = base64.b64encode(b"freshdesk-api-key:x").decode("ascii")
            json_matches = request.json() == expected
            basic_auth_header = request.headers.get("Authorization") == f"Basic {encoded_auth}"

            return json_matches and basic_auth_header

        with requests_mock.mock() as rmock:
            rmock.request(
                "POST",
                "https://freshdesk-test.com/api/v2/tickets",
                additional_matcher=match_json,
                status_code=201,
            )

            with notify_api.app_context():
                response = freshdesk.Freshdesk(
                    ContactRequest(
                        email_address="test@email.com",
                        user_profile="user_profile",
                    )
                ).send_ticket()
                assert response == 201
                email_freshdesk_ticket_mock.assert_not_called()

    def test_send_ticket_freshdesk_integration_disabled(self, mocker, email_freshdesk_ticket_mock, notify_api: Flask):
        mocked_post = mocker.patch("requests.post")
        with set_config_values(notify_api, {"FRESH_DESK_ENABLED": False}):
            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(email_address="test@email.com")).send_ticket()
                mocked_post.assert_not_called()
                email_freshdesk_ticket_mock.assert_not_called()
                assert response == 201

    def test_send_ticket_freshdesk_integration_broken(self, email_freshdesk_ticket_mock, mocker, notify_api: Flask):
        mocked_post = mocker.patch("requests.post", side_effect=RequestException)

        with set_config_values(notify_api, {"FRESH_DESK_ENABLED": True, "FRESH_DESK_API_KEY": "x"}):
            with notify_api.app_context():
                response = freshdesk.Freshdesk(ContactRequest(email_address="test@email.com")).send_ticket()
                mocked_post.assert_called_once()
                email_freshdesk_ticket_mock.assert_called_once()
                assert response == 201


class TestEmailFreshdesk:
    def test_email_freshdesk_ticket(self, mocker, notify_api: Flask, contact_form_email_template):
        mock_persist_notification = mocker.Mock()
        mock_send_notification_to_queue = mocker.Mock()
        freshdesk.persist_notification = mock_persist_notification
        freshdesk.send_notification_to_queue = mock_send_notification_to_queue

        with set_config_values(notify_api, {"CONTACT_FORM_EMAIL_ADDRESS": "contact@test.com"}):
            with notify_api.app_context():
                freshdesk_object = freshdesk.Freshdesk(ContactRequest(email_address="test@email.com"))
                content = {"data": "data"}
                freshdesk_object.email_freshdesk_ticket(content)
                mock_persist_notification.assert_called_once()
                mock_send_notification_to_queue.assert_called_once()
