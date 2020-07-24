from app import db
from app.models import ServiceSafelist


def dao_fetch_service_whitelist(service_id):
    return ServiceSafelist.query.filter(
        ServiceSafelist.service_id == service_id).all()


def dao_add_and_commit_whitelisted_contacts(objs):
    db.session.add_all(objs)
    db.session.commit()


def dao_remove_service_whitelist(service_id):
    return ServiceSafelist.query.filter(
        ServiceSafelist.service_id == service_id).delete()
