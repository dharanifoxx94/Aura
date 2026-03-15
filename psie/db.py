"""
PSIE — Database Helpers
========================
Centralised SQLite connection management with context manager.
"""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from .exceptions import DatabaseError

logger = logging.getLogger(__name__)

@contextmanager
def db_connect(db_path: str, timeout: float = 10.0) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that opens a SQLite connection with WAL mode,
    commits on clean exit, and always closes.

    Args:
        db_path: Path to SQLite database file.
        timeout: Connection timeout in seconds.

    Yields:
        sqlite3.Connection object.

    Raises:
        DatabaseError: If connection fails.
    """
    expanded = Path(db_path).expanduser()
    expanded.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(str(expanded), timeout=timeout)
    except sqlite3.Error as e:
        raise DatabaseError(f"Cannot connect to database {expanded}: {e}") from e

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise DatabaseError(f"Integrity constraint violated: {e}") from e
    except sqlite3.OperationalError as e:
        conn.rollback()
        raise DatabaseError(f"Operational DB error: {e}") from e
    except sqlite3.Error as e:
        conn.rollback()
        raise DatabaseError(f"SQLite error: {e}") from e
    except Exception as e:
        conn.rollback()
        raise DatabaseError(f"Database error: {e}") from e
    finally:
        conn.close()
