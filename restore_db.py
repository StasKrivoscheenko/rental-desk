"""Restore one verified SQLite backup. Stop the app task before running."""
import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def check_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"Database integrity check failed: {result}")


def restore(config_path, backup_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    database = Path(config["database"])
    backup = Path(backup_path)
    if not backup.is_file():
        raise FileNotFoundError(f"Backup not found: {backup}")
    check_database(backup)
    database.parent.mkdir(parents=True, exist_ok=True)
    temp = database.with_name(database.name + ".restore")
    with closing(sqlite3.connect(database)) as current:
        current.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(backup, temp)
    check_database(temp)
    for suffix in ("-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    temp.replace(database)
    print(f"Database restored from: {backup}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()
    restore(args.config, args.backup)
