"""Cross-database UUID type — works with SQLite and PostgreSQL."""
import uuid
from sqlalchemy.types import TypeDecorator, CHAR


class GUID(TypeDecorator):
    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        return value


def new_uuid():
    return str(uuid.uuid4())
