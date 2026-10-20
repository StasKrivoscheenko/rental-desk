"""Consistent SQLite online backup with integrity check and retention."""
import argparse
from contextlib import closing
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def backup(config_path, label=None):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    source = Path(config["database"])
    target_dir = Path(config["backup_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(config["data_dir"]) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_dir / "backup.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    if not source.exists():
        raise FileNotFoundError(f"Database not found: {source}")

    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    suffix = f"_{label}" if label else ""
    final = target_dir / f"rentaldb_{stamp}{suffix}.sqlite3"
    temp = target_dir / f".{final.name}.tmp"
    try:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temp)) as dst:
            src.backup(dst)
            check = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"Backup integrity check failed: {check}")
        temp.replace(final)
    except Exception:
        temp.unlink(missing_ok=True)
        logging.exception("Backup failed")
        raise

    keep_days = int(config.get("backup_retention_days", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for old in target_dir.glob("rentaldb_*.sqlite3"):
        if old == final:
            continue
        modified = datetime.fromtimestamp(old.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            old.unlink(missing_ok=True)
    logging.info("Backup completed: %s", final)
    print(f"Backup completed: {final}")
    return final


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--label", choices=("manual",))
    args = parser.parse_args()
    backup(args.config, args.label)
