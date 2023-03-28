from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from flask import current_app
from simple_salesforce import Salesforce

from .salesforce_utils import parse_result, query_one, query_param_sanitize

if TYPE_CHECKING:
    from app.models import Service

ENGAGEMENT_PRODUCT = "GC Notify"
ENGAGEMENT_TEAM = "Platform"
ENGAGEMENT_TYPE = "New Business"
ENGAGEMENT_STAGE_ACTIVATION = "Activation"
ENGAGEMENT_STAGE_LIVE = "Live"
ENGAGEMENT_STAGE_TRIAL = "Trial Account"


def create(
    session: Salesforce, service: Service, stage_name: str, account_id: Optional[str], contact_id: Optional[str]
) -> Optional[str]:
    """Create a Salesforce Engagement for the given Notify service

    Args:
        session (Salesforce): Salesforce session to perform the operation.
        service (Service): The service's details for the engagement.
        stage_name (str): The service's stage name.
        account_id (Optional[str]): Salesforce Account ID to associate with the Engagement.
        contact_id (Optional[str]): Salesforce Contact ID to associate with the Engagement.

    Returns:
       Optional[str]: Newly created Engagement ID or None if the operation failed.
    """
    engagement_id = None
    try:
        if account_id and contact_id:
            result = session.Opportunity.create(
                {
                    "Name": service.name,
                    "AccountId": account_id,
                    "ContactId": contact_id,
                    "CDS_Opportunity_Number__c": str(service.id),
                    "StageName": stage_name,
                    "CloseDate": datetime.today().strftime("%Y-%m-%d"),
                    "RecordTypeId": current_app.config["SALESFORCE_ENGAGEMENT_RECORD_TYPE"],
                    "Type": ENGAGEMENT_TYPE,
                    "CDS_Lead_Team__c": ENGAGEMENT_TEAM,
                    "Product_to_Add__c": ENGAGEMENT_PRODUCT,
                },
                headers={"Sforce-Duplicate-Rule-Header": "allowSave=true"},
            )
            parse_result(result, f"Salesforce Engagement create for service ID {service.id}")
            engagement_id = result.get("id")

            # Create the Product association
            if engagement_id:
                result = session.OpportunityLineItem.create(
                    {
                        "OpportunityId": engagement_id,
                        "PricebookEntryId": current_app.config["SALESFORCE_ENGAGEMENT_STANDARD_PRICEBOOK_ID"],
                        "Product2Id": current_app.config["SALESFORCE_ENGAGEMENT_PRODUCT_ID"],
                        "Quantity": 1,
                        "UnitPrice": 0,
                    },
                    headers={"Sforce-Duplicate-Rule-Header": "allowSave=true"},
                )
                parse_result(result, f"Salesforce Engagement OpportunityLineItem create for service ID {service.id}")
        else:
            current_app.logger.error(
                f"Salesforce Engagement create failed: missing Account ID '{account_id}' or Contact ID '{contact_id}' for service ID {service.id}"
            )
    except Exception as ex:
        current_app.logger.error(f"Salesforce Engagement create failed: {ex}")
    return engagement_id


def update_stage(
    session: Salesforce, service: Service, stage_name: str, account_id: Optional[str], contact_id: Optional[str]
) -> Optional[str]:
    """Update an Engagement's stage.  If the Engagement does not
    exist, it is created.

    Args:
        session (Salesforce): Salesforce session to perform the operation.
        service (Service): The service's details for the engagement.
        stage_name (str): The service's stage name.
        account_id (Optional[str]): Salesforce Account ID to associate with the Engagement.
        contact_id (Optional[str]): Salesforce Contact ID to associate with the Engagement.

    Returns:
        Optional[str]: Updated Engagement ID or None if the operation failed.
    """
    engagement_id = None
    try:
        engagement = get_engagement_by_service_id(session, str(service.id))

        # Existing Engagement, update the stage name
        if engagement:
            result = session.Opportunity.update(
                engagement.get("Id"),
                {"StageName": stage_name},
                headers={"Sforce-Duplicate-Rule-Header": "allowSave=true"},
            )
            is_updated = parse_result(result, f"Salesforce Engagement update '{service}'")
            engagement_id = engagement.get("Id") if is_updated else None
        # Create the Engagement.  This handles Notify services that were created before Salesforce was added.
        else:
            engagement_id = create(session, service, stage_name, account_id, contact_id)

    except Exception as ex:
        current_app.logger.error(f"Salesforce Engagement update failed: {ex}")
    return engagement_id


def get_engagement_by_service_id(session: Salesforce, service_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a Salesforce Engagement by a Notify service ID

    Args:
        session (Salesforce): Salesforce session to perform the operation.
        service_id (str): Notify service ID

    Returns:
        Optional[dict[str, str]]: Salesforce Engagement details or None if can't be found
    """
    result = None
    if isinstance(service_id, str) and service_id.strip():
        query = f"SELECT Id, Name, ContactId, AccountId FROM Opportunity where CDS_Opportunity_Number__c = '{query_param_sanitize(service_id)}' LIMIT 1"
        result = query_one(session, query)
    return result