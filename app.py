import os
import html
import csv
import io
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("RENTAL_DB", BASE_DIR / "rentals.sqlite3"))
CONTRACT_TEMPLATE = BASE_DIR / "contract_template.md"
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-deployment-" + secrets.token_hex(16))


@app.template_filter("datetimeformat")
def datetimeformat(timestamp):
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%d.%m.%Y %H:%M")


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Некоректний запит.")), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="У вас немає доступу до цієї сторінки."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Сторінку не знайдено."), 404


def db():
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, timeout=20)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    db().execute("PRAGMA journal_mode = WAL")
    db().executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          close_after TEXT
        );
        CREATE TABLE IF NOT EXISTS rentals (
          id INTEGER PRIMARY KEY, contract_no TEXT NOT NULL UNIQUE,
          customer_name TEXT NOT NULL, customer_phone TEXT DEFAULT '',
          equipment TEXT NOT NULL, price REAL NOT NULL,
          start_date TEXT NOT NULL, due_date TEXT NOT NULL,
          notes TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
          returned_at TEXT, created_by INTEGER REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    columns = {r[1] for r in db().execute("PRAGMA table_info(rentals)")}
    if "contract_data" not in columns:
        db().execute("ALTER TABLE rentals ADD COLUMN contract_data TEXT NOT NULL DEFAULT '{}' ")
    db().commit()


def utc_now():
    return datetime.now(timezone.utc)


def rental_form_groups():
    all_fields = contract_fields()
    return [("Орендодавець", [k for k in all_fields if k.startswith("lessor_")]),
            ("Орендар", [k for k in all_fields if k.startswith("renter_") and k not in ("renter_name", "renter_phone")]),
            ("Техніка", [k for k in all_fields if k.startswith("equipment_") and k != "equipment_name"]),
            ("Оплата та інші умови", [k for k in all_fields if k not in AUTO_FIELDS and k != "city" and not k.startswith(("lessor_", "renter_", "equipment_")) and k not in ("renter_name", "renter_phone")])]


def rental_defaults(rental=None):
    if rental:
        return json.loads(rental["contract_data"] or "{}")
    previous = db().execute("SELECT contract_data FROM rentals ORDER BY id DESC LIMIT 1").fetchone()
    saved = json.loads(previous["contract_data"] or "{}") if previous else {}
    return {k: v for k, v in saved.items() if k.startswith("lessor_") or k in {
        "city", "rent_period", "payment_terms", "payment_method", "deposit_return_terms",
        "late_return_penalty", "late_return_period", "claims_at_transfer", "equipment_condition",
        "equipment_condition_detailed", "equipment_configuration"
    }}


FIELD_LABELS = {
    "lessor_name":"Орендодавець — ПІБ або назва підприємства", "lessor_status":"Статус (ФОП, ТОВ тощо)",
    "lessor_tax_id":"РНОКПП / ЄДРПОУ орендодавця", "lessor_representative":"Представник орендодавця",
    "lessor_basis":"Підстава повноважень представника", "lessor_address":"Адреса орендодавця",
    "lessor_phone":"Телефон орендодавця", "lessor_email":"Електронна пошта орендодавця", "lessor_bank_details":"Банківські реквізити орендодавця",
    "renter_name":"ПІБ або назва орендаря", "renter_status":"Статус орендаря (фізична особа, ФОП, ТОВ тощо)",
    "renter_tax_id":"РНОКПП / ЄДРПОУ орендаря", "renter_document":"Паспорт / ID-картка орендаря",
    "renter_address":"Адреса орендаря", "renter_phone":"Телефон орендаря", "renter_email":"Електронна пошта орендаря",
    "renter_representative":"Представник орендаря", "equipment_name":"Найменування техніки", "equipment_type":"Тип / категорія",
    "equipment_brand":"Марка", "equipment_model":"Модель", "equipment_serial_number":"Серійний номер",
    "equipment_inventory_number":"Інвентарний номер", "equipment_year":"Рік випуску",
    "equipment_condition":"Стан під час передачі", "equipment_condition_detailed":"Детальний опис стану",
    "equipment_configuration":"Комплектація", "rent_period":"Одиниця тарифу (доба, зміна, тиждень)",
    "rent_price":"Тариф, грн за обраний період", "city":"Місто укладення договору",
    "payment_terms":"Порядок оплати", "payment_method":"Спосіб оплати", "deposit_amount":"Застава, грн",
    "deposit_return_terms":"Порядок повернення застави", "late_return_penalty":"Штраф за прострочення, грн",
    "late_return_period":"Період штрафу (доба, година тощо)", "transfer_notes":"Примітки щодо передачі",
    "claims_at_transfer":"Зауваження щодо стану під час передачі (типово: відсутні)",
}
AUTO_FIELDS = {"contract_number", "contract_date", "contract_month", "contract_year", "transfer_date", "transfer_month", "transfer_year", "rent_start_datetime", "rent_end_datetime", "total_rent_price"}
UA_MONTHS = ("січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня")


def contract_fields():
    return sorted(set(re.findall(r"{{\s*([^{}]+?)\s*}}", CONTRACT_TEMPLATE.read_text(encoding="utf-8"))) - {"equipment комплектation"})


def contract_values(row):
    values = json.loads(row["contract_data"] or "{}")
    start = row["start_date"]
    end = row["due_date"]
    today = date.today()
    def parts(value):
        parsed = date.fromisoformat(value)
        return parsed.day, UA_MONTHS[parsed.month - 1], parsed.year
    sd, sm, sy = parts(start)
    ed, em, ey = parts(end)
    td, tm, ty = today.day, UA_MONTHS[today.month - 1], today.year
    base = {
        "contract_number": row["contract_no"], "contract_date": td, "contract_month": tm, "contract_year": ty,
        "transfer_date": td, "transfer_month": tm, "transfer_year": ty,
        "rent_start_datetime": start, "rent_end_datetime": end, "total_rent_price": f"{row['price']:.2f}",
        "renter_name": row["customer_name"], "renter_phone": row["customer_phone"] or "",
        "equipment_name": row["equipment"], "rent_price": f"{row['price']:.2f}",
        "city": "", "rent_period": "добу", "payment_terms": "За домовленістю Сторін.",
        "payment_method": "Готівковий / безготівковий розрахунок.", "deposit_amount": "0",
        "deposit_return_terms": "Після повернення техніки та перевірки її стану.",
        "late_return_penalty": "0", "late_return_period": "добу", "equipment_condition": "Справний",
        "equipment_condition_detailed": "Справний стан, без видимих пошкоджень.",
        "equipment_configuration": "Згідно з фактично переданою комплектацією.",
        "claims_at_transfer": "Зауважень немає.", "renter_status": "Фізична особа",
        "renter_representative": row["customer_name"],
    }
    base.update({k: v for k, v in values.items() if v})
    # Contract fields with user-friendly defaults are set from the rental unless explicitly entered.
    base["contract_number"] = row["contract_no"]
    base["total_rent_price"] = f"{row['price']:.2f}"
    base["rent_price"] = values.get("rent_price") or f"{row['price']:.2f}"
    base["rent_start_datetime"] = start
    base["rent_end_datetime"] = end
    base["renter_name"] = values.get("renter_name") or row["customer_name"]
    base["renter_phone"] = values.get("renter_phone") or row["customer_phone"] or ""
    base["equipment_name"] = values.get("equipment_name") or row["equipment"]
    base["contract_date"], base["contract_month"], base["contract_year"] = td, tm, ty
    base["transfer_date"], base["transfer_month"], base["transfer_year"] = td, tm, ty
    return {str(k): str(v) for k, v in base.items()}


def make_contract_html(row, document_type="contract"):
    values = contract_values(row)
    source = CONTRACT_TEMPLATE.read_text(encoding="utf-8").replace("{{equipment комплектation}}", "{{equipment_configuration}}")
    act_marker = "# АКТ ПРИЙМАННЯ-ПЕРЕДАЧІ ТЕХНІКИ"
    if document_type == "act":
        source = act_marker + source.split(act_marker, 1)[1]
        document_title = "Акт приймання-передачі"
    else:
        source = source.split(act_marker, 1)[0]
        document_title = "Договір оренди"
    source = source.strip().removesuffix("---").strip()
    lines = source.splitlines()
    body, paragraph, in_list = [], [], False
    def inline(value):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(value))
    def flush_p():
        nonlocal paragraph
        if paragraph:
            body.append("<p>" + "<br>".join(inline(x) for x in paragraph) + "</p>")
            paragraph = []
    def close_list():
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_p(); close_list(); continue
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            flush_p(); close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            body.append(f"<h{min(level,3)}>{inline(stripped[level:].strip())}</h{min(level,3)}>")
        elif stripped.startswith("* "):
            flush_p()
            if not in_list: body.append("<ul>"); in_list = True
            body.append("<li>" + inline(stripped[2:]) + "</li>")
        elif stripped == "---":
            flush_p(); close_list(); body.append("<hr>")
        else:
            close_list(); paragraph.append(stripped)
    flush_p(); close_list()
    rendered = "\n".join(body)
    for key, val in values.items():
        rendered = rendered.replace("{{" + key + "}}", html.escape(val))
    rendered = re.sub(r"{{[^{}]+}}", "", rendered)
    return """<!doctype html><html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>""" + document_title + " № " + html.escape(row["contract_no"]) + """</title><style>
    body{font:12pt 'Times New Roman',serif;color:#111;max-width:850px;margin:30px auto;padding:0 32px;line-height:1.4}h1{text-align:center;font-size:16pt}h2{font-size:13pt;margin-top:22px}h3{font-size:12pt}p{margin:8px 0}ul{padding-left:28px}li{margin:4px 0}.toolbar{position:sticky;top:0;background:#fff;padding:12px 0;border-bottom:1px solid #ddd;font:14px Arial,sans-serif;display:flex;gap:10px}.toolbar button,.toolbar a{padding:9px 14px;background:#1e5eff;color:white;border:0;border-radius:5px;text-decoration:none;cursor:pointer}@media print{body{max-width:none;margin:0;padding:0 18mm}.toolbar{display:none}h2,h3{page-break-after:avoid}p,li{orphans:3;widows:3}}
    </style></head><body><div class="toolbar"><button onclick="window.print()">Друк / зберегти у PDF</button><a href="?download=1">Завантажити HTML</a></div>""" + rendered + "</body></html>"


@app.before_request
def prepare():
    if request.method == "POST":
        token = session.get("csrf_token")
        if not token or not secrets.compare_digest(token, request.form.get("csrf_token", "")):
            abort(400, "Недійсний запит. Оновіть сторінку та спробуйте ще раз.")
    g.user = None
    if session.get("user_id") and session.get("auth_token"):
        auth = db().execute("SELECT user_id,close_after FROM auth_sessions WHERE token=?", (session["auth_token"],)).fetchone()
        expired = auth and auth["close_after"] and auth["close_after"] <= utc_now().isoformat()
        if not auth or expired:
            if auth:
                db().execute("DELETE FROM auth_sessions WHERE token=?", (session["auth_token"],))
                db().commit()
            session.clear()
        else:
            g.user = db().execute("SELECT * FROM users WHERE id=? AND active=1", (auth["user_id"],)).fetchone()
            if not g.user:
                db().execute("DELETE FROM auth_sessions WHERE token=?", (session["auth_token"],))
                db().commit()
                session.clear()
            elif request.endpoint != "session_closing" and auth["close_after"] is not None:
                db().execute("UPDATE auth_sessions SET close_after=NULL WHERE token=?", (session["auth_token"],))
                db().commit()


@app.context_processor
def context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    return {"csrf_token": session["csrf_token"], "user": getattr(g, "user", None)}


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db().execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return redirect(url_for("login"))
    if request.method == "POST":
        username, password = request.form.get("username", "").strip(), request.form.get("password", "")
        if len(username) < 3 or len(password) < 10:
            flash("Ім’я користувача має містити щонайменше 3 символи, пароль — 10.", "error")
        else:
            db().execute("INSERT INTO users(username,password_hash,role) VALUES(?,?, 'admin')", (username, generate_password_hash(password)))
            db().commit()
            flash("Обліковий запис адміністратора створено. Увійдіть у систему.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not db().execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return redirect(url_for("setup"))
    if request.method == "POST":
        row = db().execute("SELECT * FROM users WHERE username=? AND active=1", (request.form.get("username", "").strip(),)).fetchone()
        if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
            session.clear()
            token = secrets.token_urlsafe(32)
            db().execute("DELETE FROM auth_sessions WHERE close_after IS NOT NULL AND close_after<=?", (utc_now().isoformat(),))
            db().execute("INSERT INTO auth_sessions(token,user_id) VALUES(?,?)", (token, row["id"]))
            db().commit()
            session["user_id"] = row["id"]
            session["auth_token"] = token
            session["csrf_token"] = secrets.token_urlsafe(24)
            return redirect(url_for("index"))
        flash("Неправильне ім’я користувача або пароль.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    db().execute("DELETE FROM auth_sessions WHERE token=?", (session.get("auth_token", ""),))
    db().commit()
    session.clear()
    return redirect(url_for("login"))


@app.post("/session/ping")
@login_required
def session_ping():
    return "", 204


@app.post("/session/closing")
@login_required
def session_closing():
    close_after = (utc_now() + timedelta(seconds=8)).isoformat()
    db().execute("UPDATE auth_sessions SET close_after=? WHERE token=?", (close_after, session["auth_token"]))
    db().commit()
    return "", 204


@app.get("/")
@login_required
def index():
    all_rows = db().execute("""SELECT r.*,u.username AS author FROM rentals r
       LEFT JOIN users u ON u.id=r.created_by ORDER BY
       CASE r.status WHEN 'active' THEN 0 ELSE 1 END, r.due_date, r.id DESC""").fetchall()
    today = date.today().isoformat()
    active = [r for r in all_rows if r["status"] == "active"]
    query = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    rows = all_rows
    if query:
        needle = query.casefold()
        rows = [r for r in rows if needle in (r["contract_no"] or "").casefold() or needle in (r["customer_name"] or "").casefold() or needle in (r["equipment"] or "").casefold()]
    if status == "active":
        rows = [r for r in rows if r["status"] == "active"]
    elif status == "returned":
        rows = [r for r in rows if r["status"] == "returned"]
    elif status == "overdue":
        rows = [r for r in rows if r["status"] == "active" and r["due_date"] < today]
    elif status == "due_today":
        rows = [r for r in rows if r["status"] == "active" and r["due_date"] == today]
    elif status != "all":
        status = "all"
    return render_template("index.html", rentals=rows, today=today, query=query, status_filter=status,
       overdue=sum(r["due_date"] < today for r in active), due_today=sum(r["due_date"] == today for r in active), active_count=len(active))


@app.get("/rentals/export")
@login_required
def export_rentals():
    all_rows = db().execute("SELECT * FROM rentals ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, due_date, id DESC").fetchall()
    query = request.args.get("q", "").strip().casefold()
    status = request.args.get("status", "all")
    today = date.today().isoformat()
    rows = all_rows
    if query:
        rows = [r for r in rows if query in (r["contract_no"] or "").casefold()
                or query in (r["customer_name"] or "").casefold()
                or query in (r["equipment"] or "").casefold()]
    if status == "active":
        rows = [r for r in rows if r["status"] == "active"]
    elif status == "returned":
        rows = [r for r in rows if r["status"] == "returned"]
    elif status == "overdue":
        rows = [r for r in rows if r["status"] == "active" and r["due_date"] < today]
    elif status == "due_today":
        rows = [r for r in rows if r["status"] == "active" and r["due_date"] == today]

    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Номер договору", "Клієнт", "Телефон", "Техніка", "Вартість, грн", "Дата передачі", "Дата повернення", "Статус", "Фактичне повернення", "Примітка"])
    def safe_cell(value):
        text = str(value or "")
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
    for row in rows:
        status_label = "Повернуто" if row["status"] == "returned" else ("Прострочено" if row["due_date"] < today else ("Повернути сьогодні" if row["due_date"] == today else "В оренді"))
        writer.writerow([safe_cell(row["contract_no"]), safe_cell(row["customer_name"]), safe_cell(row["customer_phone"]), safe_cell(row["equipment"]), f"{row['price']:.2f}", safe_cell(row["start_date"]), safe_cell(row["due_date"]), status_label, safe_cell(row["returned_at"]), safe_cell(row["notes"])])
    filename = f"rental-list-{date.today().isoformat()}.csv"
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/clients")
@login_required
def clients():
    query = request.args.get("q", "").strip()
    rows = db().execute("""SELECT customer_name, customer_phone, COUNT(*) AS rental_count,
        SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active_count,
        SUM(price) AS total_value, MAX(start_date) AS last_rental,
        MAX(id) AS latest_id FROM rentals
        GROUP BY lower(trim(customer_name)), lower(trim(coalesce(customer_phone,'')))
        ORDER BY customer_name COLLATE NOCASE""").fetchall()
    if query:
        needle = query.casefold()
        rows = [r for r in rows if needle in r["customer_name"].casefold() or needle in (r["customer_phone"] or "").casefold()]
    return render_template("clients.html", clients=rows, query=query)


@app.get("/service")
@admin_required
def service():
    backup_dir = Path(app.config.get("RENTAL_BACKUP_DIR", DB_PATH.parent / "backups"))
    backups = sorted(backup_dir.glob("rentaldb_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    check = db().execute("PRAGMA quick_check").fetchone()[0]
    size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return render_template("service.html", backups=backups, db_size=size, db_check=check,
        backup_time=app.config.get("RENTAL_BACKUP_TIME", "06:00"),
        backup_enabled=app.config.get("RENTAL_BACKUP_ENABLED", True))


@app.post("/service/backup")
@admin_required
def create_backup():
    try:
        from backup_db import backup
        config_path = os.environ.get("RENTAL_CONFIG")
        if config_path:
            created = backup(config_path, "manual")
        else:
            backup_dir = Path(app.config.get("RENTAL_BACKUP_DIR", DB_PATH.parent / "backups"))
            backup_dir.mkdir(parents=True, exist_ok=True)
            created = backup_db_file(DB_PATH, backup_dir)
        flash(f"Резервну копію створено: {created.name}", "success")
    except Exception as exc:
        app.logger.exception("Manual backup failed")
        flash(f"Не вдалося створити резервну копію: {exc}", "error")
    return redirect(url_for("service"))


def backup_db_file(source, target_dir, label="manual"):
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    target = target_dir / f"rentaldb_{stamp}_{label}.sqlite3"
    temp = target.with_name("." + target.name + ".tmp")
    try:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temp)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Перевірка цілісності копії не пройдена")
        temp.replace(target)
        return target
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def verify_backup_file(path):
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ValueError(f"Перевірка копії не пройдена: {result}")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "rentals", "auth_sessions"}.issubset(tables):
            raise ValueError("Вибраний файл не схожий на базу RentalDesk.")


@app.post("/service/restore")
@admin_required
def restore_backup():
    filename = request.form.get("backup_filename", "")
    if request.form.get("confirmation", "") != "RESTORE":
        flash("Для підтвердження відновлення введіть RESTORE.", "error")
        return redirect(url_for("service"))
    if Path(filename).name != filename or not filename.startswith("rentaldb_") or not filename.endswith(".sqlite3"):
        abort(400, "Неправильно вказано файл резервної копії.")

    backup_dir = Path(app.config.get("RENTAL_BACKUP_DIR", DB_PATH.parent / "backups")).resolve()
    selected = (backup_dir / filename).resolve()
    if selected.parent != backup_dir or not selected.is_file() or selected.resolve() == DB_PATH.resolve():
        abort(404)

    safety_copy = None
    try:
        verify_backup_file(selected)
        backup_dir.mkdir(parents=True, exist_ok=True)
        config_path = os.environ.get("RENTAL_CONFIG")
        if config_path:
            from backup_db import backup
            safety_copy = backup(config_path, "before_restore")
        else:
            safety_copy = backup_db_file(DB_PATH, backup_dir, "before_restore")

        destination = db()
        destination.commit()
        with closing(sqlite3.connect(selected.as_uri() + "?mode=ro", uri=True, timeout=20)) as source:
            source.backup(destination, pages=128, sleep=0.05)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Перевірка відновленої бази не пройдена.")
        destination.commit()
        flash(f"Базу відновлено з копії {filename}. Поточні дані збережені у {safety_copy.name}.", "success")
    except Exception as exc:
        app.logger.exception("Database restore failed")
        if safety_copy and safety_copy.is_file():
            try:
                destination = db()
                destination.commit()
                with closing(sqlite3.connect(safety_copy.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)) as source:
                    source.backup(destination, pages=128, sleep=0.05)
                destination.commit()
            except Exception:
                app.logger.exception("Failed to roll back after database restore error")
                flash("Помилка відновлення, автоматичне повернення попередньої бази також не вдалося. Збережена копія: " + safety_copy.name, "error")
                return redirect(url_for("service"))
        flash(f"Не вдалося відновити базу: {exc}. Поточні дані збережено.", "error")
    return redirect(url_for("service"))


@app.get("/service/backups/<path:filename>")
@admin_required
def download_backup(filename):
    if Path(filename).name != filename or not filename.startswith("rentaldb_") or not filename.endswith(".sqlite3"):
        abort(404)
    folder = Path(app.config.get("RENTAL_BACKUP_DIR", DB_PATH.parent / "backups")).resolve()
    target = (folder / filename).resolve()
    if target.parent != folder or not target.is_file():
        abort(404)
    return send_file(target, as_attachment=True, download_name=filename, mimetype="application/vnd.sqlite3")


BASE_RENTAL_FIELDS = ("contract_no", "customer_name", "customer_phone", "equipment", "price", "start_date", "due_date", "notes")


def rental_form_response(rental=None, posted=None):
    values = dict(rental) if rental else {}
    defaults = rental_defaults(rental)
    if posted is not None:
        for key in BASE_RENTAL_FIELDS:
            values[key] = posted.get(key, "").strip()
        for key in contract_fields():
            if key not in AUTO_FIELDS:
                defaults[key] = posted.get(key, "").strip()
    return render_template("rental_form.html", rental=values or None, contract_groups=rental_form_groups(), field_labels=FIELD_LABELS, defaults=defaults)


@app.route("/rentals/new", methods=["GET", "POST"])
@login_required
def new_rental():
    if request.method == "POST":
        fields = {k: request.form.get(k, "").strip() for k in BASE_RENTAL_FIELDS if k != "price"}
        contract_data = {k: request.form.get(k, "").strip() for k in contract_fields() if k not in AUTO_FIELDS}
        try:
            price = float(request.form.get("price", ""))
            if price < 0: raise ValueError()
            if not all(fields[k] for k in ("contract_no", "customer_name", "equipment", "start_date", "due_date")): raise ValueError()
            if fields["due_date"] < fields["start_date"]: raise ValueError()
            db().execute("""INSERT INTO rentals(contract_no,customer_name,customer_phone,equipment,price,start_date,due_date,notes,created_by,contract_data)
              VALUES(:contract_no,:customer_name,:customer_phone,:equipment,:price,:start_date,:due_date,:notes,:created_by,:contract_data)""", {**fields,"price":price,"created_by":g.user["id"],"contract_data":json.dumps(contract_data,ensure_ascii=False)})
            db().commit()
            flash("Оренду та документи створено.", "success")
            return redirect(url_for("index"))
        except sqlite3.IntegrityError:
            flash("Такий номер договору вже існує.", "error")
        except (ValueError, TypeError):
            flash("Перевірте обов’язкові поля, вартість і дати.", "error")
        return rental_form_response(posted=request.form)
    return rental_form_response()


@app.route("/rentals/<int:rental_id>/edit", methods=["GET", "POST"])
@login_required
def edit_rental(rental_id):
    rental = db().execute("SELECT * FROM rentals WHERE id=?", (rental_id,)).fetchone()
    if not rental:
        abort(404)
    if request.method == "POST":
        fields = {k: request.form.get(k, "").strip() for k in BASE_RENTAL_FIELDS if k != "price"}
        contract_data = {k: request.form.get(k, "").strip() for k in contract_fields() if k not in AUTO_FIELDS}
        try:
            price = float(request.form.get("price", ""))
            if price < 0: raise ValueError()
            if not all(fields[k] for k in ("contract_no", "customer_name", "equipment", "start_date", "due_date")): raise ValueError()
            if fields["due_date"] < fields["start_date"]: raise ValueError()
            db().execute("""UPDATE rentals SET contract_no=:contract_no,customer_name=:customer_name,customer_phone=:customer_phone,
              equipment=:equipment,price=:price,start_date=:start_date,due_date=:due_date,notes=:notes,contract_data=:contract_data WHERE id=:id""",
              {**fields,"price":price,"contract_data":json.dumps(contract_data,ensure_ascii=False),"id":rental_id})
            db().commit()
            flash("Дані оренди та документи оновлено.", "success")
            return redirect(url_for("index"))
        except sqlite3.IntegrityError:
            flash("Такий номер договору вже існує.", "error")
        except (ValueError, TypeError):
            flash("Перевірте обов’язкові поля, вартість і дати.", "error")
        return rental_form_response(rental, request.form)
    return rental_form_response(rental)


@app.get("/rentals/<int:rental_id>/document/<document_type>")
@login_required
def rental_document(rental_id, document_type):
    if document_type not in ("contract", "act"):
        abort(404)
    row = db().execute("SELECT * FROM rentals WHERE id=?", (rental_id,)).fetchone()
    if not row:
        abort(404)
    content = make_contract_html(row, document_type)
    if request.args.get("download") == "1":
        filename = re.sub(r"[^A-Za-z0-9_-]+", "_", row["contract_no"]).strip("_") or str(row["id"])
        docname = "dogovir" if document_type == "contract" else "akt-pryimannya-peredachi"
        return Response(content, mimetype="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{docname}-{filename}.html"'})
    return Response(content, mimetype="text/html; charset=utf-8")


@app.post("/rentals/<int:rental_id>/return")
@login_required
def return_rental(rental_id):
    db().execute("UPDATE rentals SET status='returned', returned_at=? WHERE id=? AND status='active'", (date.today().isoformat(), rental_id))
    db().commit()
    flash("Повернення зафіксовано.", "success")
    return redirect(url_for("index"))


@app.post("/rentals/<int:rental_id>/delete")
@admin_required
def delete_rental(rental_id):
    db().execute("DELETE FROM rentals WHERE id=?", (rental_id,))
    db().commit()
    flash("Запис оренди видалено.", "success")
    return redirect(url_for("index"))


@app.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "staff")
        if len(username) < 3 or len(password) < 10 or role not in ("admin", "staff"):
            flash("Ім’я користувача має містити щонайменше 3 символи, пароль — 10.", "error")
        else:
            try:
                db().execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", (username, generate_password_hash(password), role))
                db().commit()
                flash("Користувача додано.", "success")
            except sqlite3.IntegrityError:
                flash("Таке ім’я користувача вже існує.", "error")
        return redirect(url_for("users"))
    all_users = db().execute("SELECT id,username,role,active,created_at FROM users ORDER BY username").fetchall()
    return render_template("users.html", users=all_users)


@app.post("/users/<int:user_id>/toggle")
@admin_required
def toggle_user(user_id):
    target = db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if target and target["id"] != g.user["id"]:
        db().execute("UPDATE users SET active=? WHERE id=?", (0 if target["active"] else 1, user_id))
        db().commit()
    return redirect(url_for("users"))


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
