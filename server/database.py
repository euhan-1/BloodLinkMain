import functools
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, exc

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

# The normal engine: every endpoint that writes, and everything that needs a transaction. Its pool pre-pings each connection it hands
# out (a SELECT 1 round trip) and every use is a transaction (a BEGIN and a COMMIT/ROLLBACK round trip each).
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# The read engine: only for endpoints that never write (see main.py, "Read-only endpoints"). Each of those costs the one round trip
# of its own query instead of four, because:
#   - isolation_level="AUTOCOMMIT": no BEGIN and no COMMIT/ROLLBACK. These endpoints run READ COMMITTED, where every statement
#     already takes its own snapshot, so a transaction around a read added nothing but two round trips.
#   - no pre-ping: a dead connection is found by using it, and retry_read below runs the endpoint once more on a fresh one.
#   - every connection is put in default_transaction_read_only below, so a write that ever reached this engine fails loudly at the
#     database instead of committing on its own (autocommit has no transaction to roll back).
#   - TCP keepalives, so a connection the network silently dropped is noticed in about a minute rather than hanging on first use.
# Its own small pool: with the normal engine's 15 this stays far under Supabase's max_connections (60 measured).
read_engine = create_engine(
    DATABASE_URL,
    isolation_level="AUTOCOMMIT",
    pool_pre_ping=False,
    pool_size=4,
    max_overflow=4,
    pool_timeout=10,
    pool_recycle=1800,
    connect_args={"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
                  "application_name": "bloodlink-read"},
)


@event.listens_for(read_engine, "connect")
def _make_session_read_only(dbapi_connection, connection_record):
    # Session mode (port 5432) keeps this setting for the life of the connection; it would NOT survive a transaction-mode pooler.
    previous = dbapi_connection.autocommit
    dbapi_connection.autocommit = True
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET default_transaction_read_only = on")
    finally:
        cursor.close()
        dbapi_connection.autocommit = previous


def _worth_a_second_try(error: exc.DBAPIError) -> bool:
    """A dead connection, however the driver words it. SQLAlchemy flags the messages it knows (connection_invalidated); a pooler may
    word a closed connection differently, so any other operational/interface error also earns the one retry, except a query the
    server itself cancelled (a statement timeout, 57014), which would only time out again."""
    if error.connection_invalidated:
        return True
    if not isinstance(error, (exc.OperationalError, exc.InterfaceError)):
        return False
    return getattr(error.orig, "pgcode", None) != "57014"


def retry_read(fn):
    """Run a read-only function and, if the connection it was using turns out to be dead (the database or the pooler closed it
    while it sat idle, or Render woke from sleep with every pooled connection stale), discard the whole read pool and run it once
    more on a fresh connection. Only for functions that read and have no other side effect, which makes the second run harmless.
    A second failure (the database really is down), or any error that is not about the connection, is raised as it was."""

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except exc.DBAPIError as error:
            if not _worth_a_second_try(error):
                raise
        read_engine.dispose()  # one dead connection means the others idled just as long
        return fn(*args, **kwargs)

    return run
