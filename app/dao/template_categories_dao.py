import uuid

from sqlalchemy import asc

from app import db
from app.dao.dao_utils import transactional
from app.models import Template, TemplateCategory


@transactional
def dao_create_template_category(template_category: TemplateCategory):
    template_category.id = uuid.uuid4()
    db.session.add(template_category)


@transactional
def dao_update_template_category(template_category: TemplateCategory):
    db.session.add(template_category)


def dao_get_template_category_by_id(template_category_id) -> TemplateCategory:
    return TemplateCategory.query.filter_by(id=template_category_id).one()


def dao_get_template_category_by_template_id(template_id) -> TemplateCategory:
    return Template.query.filter_by(id=template_id).one().template_category


# TODO: Add filters: Select all template categories used by at least 1 sms/email template
def dao_get_all_template_categories(template_type=None, hidden=False):
    return TemplateCategory.query.order_by(asc(TemplateCategory.name_en)).all()
