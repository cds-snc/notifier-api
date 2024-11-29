from datetime import datetime, timezone

from flask import current_app
from itsdangerous import BadSignature

from app import create_uuid, db, signer_bearer_token
from app.dao.dao_utils import transactional, version_class
from app.models import (
    COMPLAINT_CALLBACK_TYPE,
    DELIVERY_STATUS_CALLBACK_TYPE,
    ServiceCallbackApi,
)


@transactional
def resign_service_callbacks(resign: bool, unsafe: bool = False):
    """Resign the _bearer_token column of the service_callbacks table with (potentially) a new key.

    Args:
        resign (bool): whether to resign the service_callbacks
        unsafe (bool, optional): resign regardless of whether the unsign step fails with a BadSignature.
        Defaults to False.

    Raises:
        e: BadSignature if the unsign step fails and unsafe is False.
    """
    rows = ServiceCallbackApi.query.all()  # noqa
    current_app.logger.info(f"Total of {len(rows)} service callbacks")
    rows_to_update = []

    for row in rows:
        if row._bearer_token:
            try:
                old_signature = row._bearer_token
                unsigned_token = getattr(row, "bearer_token")  # unsign the token
            except BadSignature as e:
                if unsafe:
                    unsigned_token = signer_bearer_token.verify_unsafe(row._bearer_token)
                else:
                    current_app.logger.error(f"BadSignature for service_callback {row.id}")
                    raise e
            setattr(row, "bearer_token", unsigned_token)  # resigns the token with (potentially) a new signing secret
            if old_signature != row._bearer_token:
                rows_to_update.append(row)
            if not resign:
                row._bearer_token = old_signature  # reset the signature to the old value

    if resign:
        current_app.logger.info(f"Resigning {len(rows_to_update)} service callbacks")
        db.session.bulk_save_objects(rows)
    elif not resign:
        current_app.logger.info(f"{len(rows_to_update)} service callbacks need resigning")


@transactional
@version_class(ServiceCallbackApi)
def save_service_callback_api(service_callback_api):
    service_callback_api.id = create_uuid() if not service_callback_api.id else service_callback_api.id
    service_callback_api.created_at = datetime.utcnow()
    db.session.add(service_callback_api)


@transactional
@version_class(ServiceCallbackApi)
def reset_service_callback_api(service_callback_api, updated_by_id, url=None, bearer_token=None):
    if url:
        service_callback_api.url = url
    if bearer_token:
        service_callback_api.bearer_token = bearer_token
    service_callback_api.updated_by_id = updated_by_id
    service_callback_api.updated_at = datetime.utcnow()

    db.session.add(service_callback_api)


def get_service_callback_api_with_service_id(service_id) -> ServiceCallbackApi:
    # There is ONLY one callback configured per service
    return ServiceCallbackApi.query.filter_by(service_id=service_id).all()


def get_service_callback_api(service_callback_api_id, service_id) -> ServiceCallbackApi:
    return ServiceCallbackApi.query.filter_by(id=service_callback_api_id, service_id=service_id).first()


def get_service_delivery_status_callback_api_for_service(service_id) -> ServiceCallbackApi:
    return ServiceCallbackApi.query.filter_by(service_id=service_id, callback_type=DELIVERY_STATUS_CALLBACK_TYPE).first()


def get_service_complaint_callback_api_for_service(service_id) -> ServiceCallbackApi:
    return ServiceCallbackApi.query.filter_by(service_id=service_id, callback_type=COMPLAINT_CALLBACK_TYPE).first()


@transactional
def delete_service_callback_api(service_callback_api):
    db.session.delete(service_callback_api)


# Used by Cypress to clean up test data
@transactional
def delete_service_callback_api_history(service_callback_api: ServiceCallbackApi):
    callback_history = (
        service_callback_api.get_history_model()
        .query.filter_by(service_id=service_callback_api.service_id, id=service_callback_api.id)
        .all()
    )
    for history in callback_history:
        db.session.delete(history)


@transactional
@version_class(ServiceCallbackApi)
def suspend_unsuspend_service_callback_api(service_callback_api, updated_by_id, suspend=False):
    service_callback_api.is_suspended = suspend
    service_callback_api.suspended_at = datetime.now(timezone.utc)
    service_callback_api.updated_by_id = updated_by_id
    service_callback_api.updated_at = datetime.now(timezone.utc)
    db.session.add(service_callback_api)
