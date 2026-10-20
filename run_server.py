"""Production entry point for the Windows on-premise installation."""
import argparse
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    data_dir = Path(config["data_dir"])
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "server.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s")

    os.environ["RENTAL_DB"] = config["database"]
    os.environ["SECRET_KEY"] = config["secret_key"]
    os.environ["RENTAL_CONFIG"] = str(Path(args.config).resolve())
    from waitress import serve
    from app import app, init_db
    app.config["RENTAL_BACKUP_DIR"] = config["backup_dir"]
    app.config["RENTAL_BACKUP_RETENTION_DAYS"] = int(config.get("backup_retention_days", 30))
    app.config["RENTAL_BACKUP_TIME"] = config.get("backup_time", "06:00")
    app.config["RENTAL_BACKUP_ENABLED"] = bool(config.get("backup_enabled", True))

    logging.getLogger("rentaldesk").info("Starting rental application on %s:%s", config["bind_host"], config["port"])
    logging.getLogger("waitress").setLevel(logging.INFO)
    with app.app_context():
        init_db()
    serve(app, host=config["bind_host"], port=int(config["port"]), threads=8, ident="RentalDesk")


if __name__ == "__main__":
    main()
