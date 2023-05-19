from functools import cached_property, partial
from typing import Any, Optional

import greenlet  # type: ignore
import sqlalchemy.types as types
from flask_sqlalchemy import BaseQuery, SignallingSession, SQLAlchemy
from sqlalchemy import orm

def get_state(app):
    """Gets the state for the application"""
    assert 'sqlalchemy' in app.extensions, \
        'The sqlalchemy extension was not registered to the current ' \
        'application.  Please make sure to call init_app() first.'
    return app.extensions['sqlalchemy']

# adapted from https://r2c.dev/blog/2020/fixing-leaky-logs-how-to-find-a-bug-and-ensure-it-never-returns/
class SensitiveString(types.TypeDecorator):
    """
    String column type for use with SQLAlchemy models whose
    content should not appear in logs or exceptions
    """

    impl = types.String

    class Repr(str):
        def __repr__(self) -> str:
            return "********"

    def process_bind_param(self, value: Optional[str], dialect: Any) -> Optional[Repr]:
        return self.Repr(value) if value else None

    def process_result_value(self, value: Optional[Repr], dialect: Any) -> Optional[str]:
        return str(value) if value else None


class ExplicitRoutingSession(SignallingSession):
    """
    This session implementation will route to explicitly named bind.
    If no bind is mentioned with the session via the `using_bind` function,
    then the `reader` bind will get returned instead.
    """

    _name: Optional[str] = None

    def get_bind(self, mapper=None, clause=None):
        # If reader and writer binds are not configured,
        # connect using the default SQLALCHEMY_DATABASE_URI
        if not self.binds_setup:
            return super().get_bind(mapper, clause)

        return self.load_balance(mapper, clause)

    def load_balance(self, mapper=None, clause=None):
        # Use the explicit name if present
        if self._name and not self._flushing:
            bind = self._name
            self._name = None
            self.app.logger.debug(f"Connecting -> {bind}")
            return get_state(self.app).db.get_engine(self.app, bind=bind)

        # Everything else goes to the writer engine
        else:
            self.app.logger.debug("Connecting -> writer")
            return get_state(self.app).db.get_engine(self.app, bind="writer")

    def using_bind(self, name: str):
        self._name = name
        return self

    @cached_property
    def binds_setup(self):
        binds = self.app.config["SQLALCHEMY_BINDS"] or {}
        return all([k in binds for k in ["reader", "writer"]])


class RoutingSQLAlchemy(SQLAlchemy):

    SensitiveString = SensitiveString

    def on_reader(self):
        return self.session().using_bind("reader")

    def create_scoped_session(self, options=None):
        options = options or {}
        options.setdefault("query_cls", BaseQuery)

        return orm.scoped_session(partial(ExplicitRoutingSession, self, **options), scopefunc=greenlet.getcurrent)
