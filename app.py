import csv
import io
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
CONTACT_STATUSES = {"Lead", "Prospect", "Customer", "Inactive"}
DEAL_STAGES = {"Prospecting", "Qualified", "Proposal", "Negotiation", "Won", "Lost"}
TASK_PRIORITIES = {"Low", "Medium", "High"}
TASK_STATUSES = {"Open", "In progress", "Done"}
ACTIVITY_KINDS = {"Note", "Call", "Email", "Meeting"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("CRM_DATABASE", str(BASE_DIR / "instance" / "crm.db")),
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    def connect_db():
        db = sqlite3.connect(app.config["DATABASE"])
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @contextmanager
    def db_session():
        db = connect_db()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize_db():
        with db_session() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    website TEXT NOT NULL DEFAULT '',
                    industry TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    job_title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Lead',
                    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    value REAL NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'Prospecting',
                    expected_close TEXT NOT NULL DEFAULT '',
                    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
                    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    due_date TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'Medium',
                    status TEXT NOT NULL DEFAULT 'Open',
                    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
                    deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'Note',
                    subject TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
                    deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
                CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);
                CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
                CREATE INDEX IF NOT EXISTS idx_activities_created ON activities(created_at);
                """
            )

    initialize_db()

    def error(message, status=400):
        return jsonify({"error": message}), status

    def json_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("El cuerpo debe ser un objeto JSON.")
        return body

    def clean_text(value, field, required=False, limit=2000):
        value = "" if value is None else str(value).strip()
        if required and not value:
            raise ValueError(f"El campo «{field}» es obligatorio.")
        if len(value) > limit:
            raise ValueError(f"El campo «{field}» supera el límite de {limit} caracteres.")
        return value

    def optional_id(value, field):
        if value in (None, ""):
            return None
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"El campo «{field}» debe ser un identificador válido.")
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"El campo «{field}» debe ser un identificador válido.")
        if result < 1:
            raise ValueError(f"El campo «{field}» debe ser un identificador válido.")
        return result

    def validate_date(value, field):
        value = clean_text(value, field, limit=10)
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"El campo «{field}» debe tener formato AAAA-MM-DD.")
            if parsed.isoformat() != value:
                raise ValueError(f"El campo «{field}» debe tener formato AAAA-MM-DD.")
        return value

    def safe_csv_value(value):
        value = "" if value is None else str(value)
        if value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
            return "'" + value
        return value

    def rows(sql, params=()):
        with db_session() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def remove(table, record_id):
        with db_session() as db:
            cursor = db.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
            return cursor.rowcount > 0

    def search_query():
        value = request.args.get("q", "").strip()
        if len(value) > 200:
            return None
        return value

    def search_pattern(query):
        escaped = query.replace("~", "~~").replace("%", "~%").replace("_", "~_")
        return f"%{escaped}%"

    def ensure_references(db, **references):
        for table, record_id in references.items():
            if record_id is not None and not db.execute(
                f"SELECT id FROM {table} WHERE id = ?", (record_id,)
            ).fetchone():
                raise ValueError(f"No existe el registro relacionado en «{table}».")

    def save_record(table, record_id, fields, values, references=None):
        with db_session() as db:
            ensure_references(db, **(references or {}))
            if record_id is None:
                columns = ", ".join(fields)
                placeholders = ", ".join("?" for _ in fields)
                cursor = db.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values
                )
                new_id = cursor.lastrowid
            else:
                assignments = ", ".join(f"{field} = ?" for field in fields)
                cursor = db.execute(
                    f"UPDATE {table} SET {assignments} WHERE id = ?",
                    [*values, record_id],
                )
                if cursor.rowcount == 0:
                    return None
                new_id = record_id
            return dict(db.execute(f"SELECT * FROM {table} WHERE id = ?", (new_id,)).fetchone())

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    @app.get("/api/dashboard")
    def dashboard():
        with db_session() as db:
            totals = {
                "contacts": db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                "companies": db.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
                "open_deals": db.execute(
                    "SELECT COUNT(*) FROM deals WHERE stage NOT IN ('Won', 'Lost')"
                ).fetchone()[0],
                "pipeline_value": db.execute(
                    "SELECT COALESCE(SUM(value), 0) FROM deals WHERE stage NOT IN ('Won', 'Lost')"
                ).fetchone()[0],
                "open_tasks": db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status != 'Done'"
                ).fetchone()[0],
            }
            stages = [
                dict(item)
                for item in db.execute(
                    """SELECT stage, COUNT(*) AS count, COALESCE(SUM(value), 0) AS value
                       FROM deals GROUP BY stage ORDER BY
                       CASE stage WHEN 'Prospecting' THEN 1 WHEN 'Qualified' THEN 2
                         WHEN 'Proposal' THEN 3 WHEN 'Negotiation' THEN 4
                         WHEN 'Won' THEN 5 ELSE 6 END"""
                ).fetchall()
            ]
            recent = [
                dict(item)
                for item in db.execute(
                    """SELECT a.*, c.first_name || ' ' || c.last_name AS contact_name
                       FROM activities a LEFT JOIN contacts c ON c.id = a.contact_id
                       ORDER BY a.created_at DESC, a.id DESC LIMIT 6"""
                ).fetchall()
            ]
            upcoming = [
                dict(item)
                for item in db.execute(
                    """SELECT t.*, c.first_name || ' ' || c.last_name AS contact_name
                       FROM tasks t LEFT JOIN contacts c ON c.id = t.contact_id
                       WHERE t.status != 'Done' ORDER BY
                         CASE WHEN t.due_date = '' THEN 1 ELSE 0 END,
                         t.due_date ASC, t.id DESC LIMIT 6"""
                ).fetchall()
            ]
        return jsonify({**totals, "stages": stages, "recent_activities": recent, "upcoming_tasks": upcoming})

    @app.get("/api/contacts")
    def list_contacts():
        query = search_query()
        if query is None:
            return error("La búsqueda no puede superar 200 caracteres.")
        pattern = search_pattern(query)
        return jsonify(rows(
            """SELECT c.*, co.name AS company_name FROM contacts c
               LEFT JOIN companies co ON co.id = c.company_id
               WHERE ? = '' OR c.first_name LIKE ? ESCAPE '~'
                  OR c.last_name LIKE ? ESCAPE '~' OR c.email LIKE ? ESCAPE '~'
                  OR c.phone LIKE ? ESCAPE '~' OR co.name LIKE ? ESCAPE '~'
               ORDER BY c.created_at DESC, c.id DESC""",
            (query, pattern, pattern, pattern, pattern, pattern),
        ))

    @app.route("/api/contacts", methods=["POST"])
    @app.route("/api/contacts/<int:record_id>", methods=["PUT"])
    def save_contact(record_id=None):
        try:
            data = json_body()
            fields = ["first_name", "last_name", "email", "phone", "job_title", "status", "company_id", "notes"]
            email = clean_text(data.get("email"), "email", limit=254)
            status = clean_text(data.get("status", "Lead"), "estado", True, 30)
            company_id = optional_id(data.get("company_id"), "empresa")
            values = [
                clean_text(data.get("first_name"), "nombre", True, 100),
                clean_text(data.get("last_name"), "apellido", True, 100),
                email,
                clean_text(data.get("phone"), "teléfono", limit=60),
                clean_text(data.get("job_title"), "cargo", limit=120),
                status,
                company_id,
                clean_text(data.get("notes"), "notas"),
            ]
            if email and not EMAIL_PATTERN.fullmatch(email):
                raise ValueError("El campo «email» debe contener una dirección válida.")
            if status not in CONTACT_STATUSES:
                raise ValueError("El estado del contacto seleccionado no es válido.")
            saved = save_record("contacts", record_id, fields, values, {"companies": company_id})
            if saved is None:
                return error("Contacto no encontrado.", 404)
            return jsonify(saved), 201 if record_id is None else 200
        except ValueError as exc:
            return error(str(exc))
        except sqlite3.IntegrityError as exc:
            return error(f"No se pudo guardar el contacto: {exc}")

    @app.delete("/api/contacts/<int:record_id>")
    def delete_contact(record_id):
        if not remove("contacts", record_id):
            return error("Contacto no encontrado.", 404)
        return jsonify({"deleted": True})

    @app.get("/api/companies")
    def list_companies():
        query = search_query()
        if query is None:
            return error("La búsqueda no puede superar 200 caracteres.")
        pattern = search_pattern(query)
        return jsonify(rows(
            """SELECT co.*, (SELECT COUNT(*) FROM contacts c WHERE c.company_id = co.id) AS contact_count,
                      (SELECT COUNT(*) FROM deals d WHERE d.company_id = co.id) AS deal_count
               FROM companies co WHERE ? = '' OR co.name LIKE ? ESCAPE '~'
                 OR co.industry LIKE ? ESCAPE '~' OR co.city LIKE ? ESCAPE '~'
               ORDER BY co.name COLLATE NOCASE""",
            (query, pattern, pattern, pattern),
        ))

    @app.route("/api/companies", methods=["POST"])
    @app.route("/api/companies/<int:record_id>", methods=["PUT"])
    def save_company(record_id=None):
        try:
            data = json_body()
            fields = ["name", "website", "industry", "phone", "city", "notes"]
            values = [
                clean_text(data.get("name"), "nombre", True, 160),
                clean_text(data.get("website"), "sitio web", limit=254),
                clean_text(data.get("industry"), "sector", limit=100),
                clean_text(data.get("phone"), "teléfono", limit=60),
                clean_text(data.get("city"), "ciudad", limit=100),
                clean_text(data.get("notes"), "notas"),
            ]
            saved = save_record("companies", record_id, fields, values)
            if saved is None:
                return error("Empresa no encontrada.", 404)
            return jsonify(saved), 201 if record_id is None else 200
        except ValueError as exc:
            return error(str(exc))

    @app.delete("/api/companies/<int:record_id>")
    def delete_company(record_id):
        if not remove("companies", record_id):
            return error("Empresa no encontrada.", 404)
        return jsonify({"deleted": True})

    @app.get("/api/deals")
    def list_deals():
        query = search_query()
        if query is None:
            return error("La búsqueda no puede superar 200 caracteres.")
        pattern = search_pattern(query)
        return jsonify(rows(
            """SELECT d.*, c.first_name || ' ' || c.last_name AS contact_name, co.name AS company_name
               FROM deals d LEFT JOIN contacts c ON c.id = d.contact_id
               LEFT JOIN companies co ON co.id = d.company_id
               WHERE ? = '' OR d.title LIKE ? ESCAPE '~' OR c.first_name LIKE ? ESCAPE '~'
                 OR c.last_name LIKE ? ESCAPE '~' OR co.name LIKE ? ESCAPE '~'
               ORDER BY d.created_at DESC, d.id DESC""",
            (query, pattern, pattern, pattern, pattern),
        ))

    @app.route("/api/deals", methods=["POST"])
    @app.route("/api/deals/<int:record_id>", methods=["PUT"])
    def save_deal(record_id=None):
        try:
            data = json_body()
            fields = ["title", "value", "stage", "expected_close", "contact_id", "company_id", "notes"]
            if isinstance(data.get("value", 0), bool):
                raise ValueError("El valor de la oportunidad debe ser numérico.")
            try:
                value = float(data.get("value", 0) or 0)
            except (TypeError, ValueError):
                raise ValueError("El valor de la oportunidad debe ser numérico.")
            if not math.isfinite(value):
                raise ValueError("El valor de la oportunidad debe ser un número finito.")
            if value < 0:
                raise ValueError("El valor de la oportunidad no puede ser negativo.")
            stage = clean_text(data.get("stage", "Prospecting"), "etapa", True, 30)
            if stage not in DEAL_STAGES:
                raise ValueError("La etapa seleccionada no es válida.")
            contact_id = optional_id(data.get("contact_id"), "contacto")
            company_id = optional_id(data.get("company_id"), "empresa")
            values = [
                clean_text(data.get("title"), "título", True, 180),
                value,
                stage,
                validate_date(data.get("expected_close"), "fecha de cierre"),
                contact_id,
                company_id,
                clean_text(data.get("notes"), "notas"),
            ]
            saved = save_record(
                "deals", record_id, fields, values,
                {"contacts": contact_id, "companies": company_id},
            )
            if saved is None:
                return error("Oportunidad no encontrada.", 404)
            return jsonify(saved), 201 if record_id is None else 200
        except ValueError as exc:
            return error(str(exc))

    @app.delete("/api/deals/<int:record_id>")
    def delete_deal(record_id):
        if not remove("deals", record_id):
            return error("Oportunidad no encontrada.", 404)
        return jsonify({"deleted": True})

    @app.get("/api/tasks")
    def list_tasks():
        query = search_query()
        if query is None:
            return error("La búsqueda no puede superar 200 caracteres.")
        pattern = search_pattern(query)
        return jsonify(rows(
            """SELECT t.*, c.first_name || ' ' || c.last_name AS contact_name, d.title AS deal_title
               FROM tasks t LEFT JOIN contacts c ON c.id = t.contact_id
               LEFT JOIN deals d ON d.id = t.deal_id
               WHERE ? = '' OR t.title LIKE ? ESCAPE '~' OR t.description LIKE ? ESCAPE '~'
                 OR c.first_name LIKE ? ESCAPE '~' OR c.last_name LIKE ? ESCAPE '~'
               ORDER BY CASE t.status WHEN 'Open' THEN 0 ELSE 1 END,
                 CASE WHEN t.due_date = '' THEN 1 ELSE 0 END, t.due_date, t.id DESC""",
            (query, pattern, pattern, pattern, pattern),
        ))

    @app.route("/api/tasks", methods=["POST"])
    @app.route("/api/tasks/<int:record_id>", methods=["PUT"])
    def save_task(record_id=None):
        try:
            data = json_body()
            priority = clean_text(data.get("priority", "Medium"), "prioridad", True, 20)
            status = clean_text(data.get("status", "Open"), "estado", True, 20)
            if priority not in TASK_PRIORITIES:
                raise ValueError("La prioridad seleccionada no es válida.")
            if status not in TASK_STATUSES:
                raise ValueError("El estado seleccionado no es válido.")
            contact_id = optional_id(data.get("contact_id"), "contacto")
            deal_id = optional_id(data.get("deal_id"), "oportunidad")
            fields = ["title", "description", "due_date", "priority", "status", "contact_id", "deal_id"]
            values = [
                clean_text(data.get("title"), "título", True, 180),
                clean_text(data.get("description"), "descripción"),
                validate_date(data.get("due_date"), "fecha límite"),
                priority, status, contact_id, deal_id,
            ]
            saved = save_record(
                "tasks", record_id, fields, values,
                {"contacts": contact_id, "deals": deal_id},
            )
            if saved is None:
                return error("Tarea no encontrada.", 404)
            return jsonify(saved), 201 if record_id is None else 200
        except ValueError as exc:
            return error(str(exc))

    @app.delete("/api/tasks/<int:record_id>")
    def delete_task(record_id):
        if not remove("tasks", record_id):
            return error("Tarea no encontrada.", 404)
        return jsonify({"deleted": True})

    @app.get("/api/activities")
    def list_activities():
        query = search_query()
        if query is None:
            return error("La búsqueda no puede superar 200 caracteres.")
        pattern = search_pattern(query)
        return jsonify(rows(
            """SELECT a.*, c.first_name || ' ' || c.last_name AS contact_name, d.title AS deal_title
               FROM activities a LEFT JOIN contacts c ON c.id = a.contact_id
               LEFT JOIN deals d ON d.id = a.deal_id
               WHERE ? = '' OR a.subject LIKE ? ESCAPE '~' OR a.details LIKE ? ESCAPE '~'
                 OR c.first_name LIKE ? ESCAPE '~' OR c.last_name LIKE ? ESCAPE '~'
                 OR d.title LIKE ? ESCAPE '~'
               ORDER BY a.created_at DESC, a.id DESC""",
            (query, pattern, pattern, pattern, pattern, pattern),
        ))

    @app.post("/api/activities")
    def create_activity():
        try:
            data = json_body()
            kind = clean_text(data.get("kind", "Note"), "tipo", True, 30)
            if kind not in ACTIVITY_KINDS:
                raise ValueError("El tipo de actividad seleccionado no es válido.")
            contact_id = optional_id(data.get("contact_id"), "contacto")
            deal_id = optional_id(data.get("deal_id"), "oportunidad")
            fields = ["kind", "subject", "details", "contact_id", "deal_id"]
            values = [
                kind,
                clean_text(data.get("subject"), "asunto", True, 180),
                clean_text(data.get("details"), "detalles"),
                contact_id, deal_id,
            ]
            saved = save_record(
                "activities", None, fields, values,
                {"contacts": contact_id, "deals": deal_id},
            )
            return jsonify(saved), 201
        except ValueError as exc:
            return error(str(exc))

    @app.delete("/api/activities/<int:record_id>")
    def delete_activity(record_id):
        if not remove("activities", record_id):
            return error("Actividad no encontrada.", 404)
        return jsonify({"deleted": True})

    @app.get("/api/export/contacts.csv")
    def export_contacts():
        records = rows(
            """SELECT c.first_name, c.last_name, c.email, c.phone, c.job_title, c.status,
                      co.name AS company, c.notes, c.created_at
               FROM contacts c LEFT JOIN companies co ON co.id = c.company_id
               ORDER BY c.last_name, c.first_name"""
        )
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=[
            "first_name", "last_name", "email", "phone", "job_title",
            "status", "company", "notes", "created_at",
        ])
        writer.writeheader()
        writer.writerows(
            {key: safe_csv_value(value) for key, value in record.items()}
            for record in records
        )
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=contactos.csv"},
        )

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return error("Recurso no encontrado.", 404)
        return "Página no encontrada", 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
