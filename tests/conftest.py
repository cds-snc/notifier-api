import os
from contextlib import contextmanager
from typing import List
from urllib.parse import urlparse

import pytest
import sqlalchemy
from alembic.command import upgrade
from alembic.config import Config
from flask import Flask

from app import create_app, db
from app.encryption import CryptoSigner


def pytest_configure(config):
    # Swap to test database if running from devcontainer
    if os.environ.get("SQLALCHEMY_DATABASE_TEST_URI") is not None:
        os.environ["SQLALCHEMY_DATABASE_URI"] = os.environ.get("SQLALCHEMY_DATABASE_TEST_URI")
        os.environ["SQLALCHEMY_DATABASE_READER_URI"] = os.environ.get("SQLALCHEMY_DATABASE_TEST_URI")


@pytest.fixture(scope="session")
def notify_api():
    app = Flask("test")
    create_app(app)

    # deattach server-error error handlers - error_handler_spec looks like:
    #   {'blueprint_name': {
    #       status_code: [error_handlers],
    #       None: { ExceptionClass: error_handler }
    # }}
    for error_handlers in app.error_handler_spec.values():
        error_handlers.pop(500, None)
        if None in error_handlers:
            error_handlers[None] = {
                exc_class: error_handler
                for exc_class, error_handler in error_handlers[None].items()
                if exc_class is not Exception
            }
            if error_handlers[None] == []:
                error_handlers.pop(None)

    ctx = app.app_context()
    ctx.push()

    yield app

    ctx.pop()


@pytest.fixture(scope="function")
def client(notify_api):
    with notify_api.test_request_context(), notify_api.test_client() as client:
        yield client


def create_test_db(writer_uri):
    db_uri_parts = writer_uri.split("/")
    db_uri = "/".join(db_uri_parts[:-1] + ["postgres"])
    db_name = db_uri_parts[-1]

    postgres_db = sqlalchemy.create_engine(db_uri, echo=False, isolation_level="AUTOCOMMIT", client_encoding="utf8")
    try:
        postgres_db.execute(sqlalchemy.sql.text(f"CREATE DATABASE {db_name};")).close()
    except sqlalchemy.exc.ProgrammingError:
        # database "test_notification_api_master" already exists
        pass
    finally:
        postgres_db.dispose()


def grant_test_db(writer_uri, uri_db_reader):
    db_schema = "public"
    db_reader = urlparse(uri_db_reader).username
    db_reader_password = urlparse(uri_db_reader).password

    postgres_db = sqlalchemy.create_engine(writer_uri, echo=False, isolation_level="AUTOCOMMIT", client_encoding="utf8")

    statements = [
        f"CREATE ROLE {db_reader} LOGIN PASSWORD '{db_reader_password}';",
        f"GRANT USAGE ON SCHEMA {db_schema} TO {db_reader};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {db_schema} TO {db_reader};",
    ]
    for statement in statements:
        try:
            postgres_db.execute(sqlalchemy.sql.text(statement)).close()
        except sqlalchemy.exc.ProgrammingError:
            pass
    postgres_db.dispose()


@pytest.fixture(scope="session")
def notify_db(notify_api, worker_id):
    assert "test_notification_api" in db.engine.url.database, "dont run tests against main db"

    # create a database for this worker thread -
    from flask import current_app

    current_app.config["SQLALCHEMY_DATABASE_URI"] += "_{}".format(worker_id)
    current_app.config["SQLALCHEMY_DATABASE_READER_URI"] += "_{}".format(worker_id)
    uri_db_writer = current_app.config["SQLALCHEMY_DATABASE_URI"]
    uri_db_reader = current_app.config["SQLALCHEMY_DATABASE_READER_URI"]
    current_app.config["SQLALCHEMY_BINDS"] = {
        "reader": uri_db_reader,
        "writer": uri_db_writer,
    }

    create_test_db(uri_db_writer)

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))
    ALEMBIC_CONFIG = os.path.join(BASE_DIR, "migrations")
    config = Config(ALEMBIC_CONFIG + "/alembic.ini")
    config.set_main_option("script_location", ALEMBIC_CONFIG)

    with notify_api.app_context():
        upgrade(config, "head")

    grant_test_db(uri_db_writer, uri_db_reader)

    yield db

    db.session.remove()
    db.get_engine(notify_api).dispose()


@pytest.fixture(scope="function")
def notify_db_session(notify_db):
    yield notify_db

    notify_db.session.remove()
    for tbl in reversed(notify_db.metadata.sorted_tables):
        if tbl.name not in [
            "provider_details",
            "key_types",
            "branding_type",
            "job_status",
            "provider_details_history",
            "template_process_type",
            "notification_status_types",
            "organisation_types",
            "service_permission_types",
            "auth_type",
            "invite_status_type",
            "service_callback_type",
        ]:
            notify_db.engine.execute(tbl.delete())
    notify_db.session.commit()


@pytest.fixture
def os_environ():
    """
    clear os.environ, and restore it after the test runs
    """
    # for use whenever you expect code to edit environment variables
    old_env = os.environ.copy()

    class EnvironDict(dict):
        def __setitem__(self, key, value):
            assert isinstance(value, str)
            super().__setitem__(key, value)

    os.environ = EnvironDict()
    yield
    os.environ = old_env


def pytest_generate_tests(metafunc):
    # Copied from https://gist.github.com/pfctdayelise/5719730
    idparametrize = metafunc.definition.get_closest_marker("idparametrize")
    if idparametrize:
        argnames, testdata = idparametrize.args
        ids, argvalues = zip(*sorted(testdata.items()))
        metafunc.parametrize(argnames, argvalues, ids=ids)


@contextmanager
def set_config(app, name, value):
    old_val = app.config.get(name)
    app.config[name] = value
    try:
        yield
    finally:
        app.config[name] = old_val


@contextmanager
def set_config_values(app, dict):
    old_values = {}

    for key in dict:
        old_values[key] = app.config.get(key)
        app.config[key] = dict[key]

    try:
        yield
    finally:
        for key in dict:
            app.config[key] = old_values[key]


@contextmanager
def set_signer_secret_key(signer: CryptoSigner, secret_key: str | List[str]):
    old_secret_key = signer.secret_key
    signer.init_app(signer.app, secret_key, signer.salt)
    try:
        yield
    finally:
        signer.init_app(signer.app, old_secret_key, signer.salt)


class Matcher:
    def __init__(self, description, key):
        self.description = description
        self.key = key

    def __eq__(self, other):
        return self.key(other)

    def __repr__(self):
        return "<Matcher: {}>".format(self.description)
