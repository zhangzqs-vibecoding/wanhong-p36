"""Store guest SD writes separately from the read-only base image."""

from contextlib import closing, contextmanager
from fcntl import LOCK_EX, LOCK_NB, flock
import os
import sqlite3


class SavedSectors:
    def __init__(self, connection):
        self.connection = connection

    def read(self, sector, count):
        return self.connection.execute(
            "SELECT lba, data FROM sectors WHERE lba >= ? AND lba < ?",
            (sector, sector + count),
        )

    def write(self, sectors):
        with self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO sectors (lba, data) VALUES (?, ?)", sectors
            )


@contextmanager
def open_sd_state(image, path):
    base = os.fstat(image.fileno())
    signature = f"{base.st_dev}:{base.st_ino}:{base.st_size}:{base.st_mtime_ns}"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        if os.path.samestat(base, os.fstat(lock.fileno())):
            raise ValueError("SD state and base image must be separate files")
        try:
            flock(lock, LOCK_EX | LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"SD state is already in use: {path}") from error
        # The web server creates the connection and lends it to its one emulator thread.
        with closing(sqlite3.connect(path, check_same_thread=False)) as connection:
            tables = {
                name for name, in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not tables:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("CREATE TABLE base_image (signature TEXT NOT NULL)")
                    connection.execute(
                        "CREATE TABLE sectors (lba INTEGER PRIMARY KEY, "
                        "data BLOB NOT NULL CHECK (length(data) = 512))"
                    )
                    connection.execute("INSERT INTO base_image VALUES (?)", (signature,))
            elif tables != {"base_image", "sectors"}:
                raise ValueError(f"Invalid P36 SD state database: {path}")
            elif connection.execute("SELECT signature FROM base_image").fetchall() != [(signature,)]:
                raise ValueError(f"SD state belongs to a different or modified base image: {path}")
            yield SavedSectors(connection)
