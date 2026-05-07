import csv
import hashlib
import io
import json
import sqlite3
import mimetypes
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.backup_io import (
    build_full_backup_payload,
    compact_active_client_ids,
    restore_from_payload,
    write_readable_excel,
)
from app.database import BASE_DIR as APP_STORAGE_ROOT, get_db, init_db
from app.schemas import (
    ClientCreate,
    ClientUpdate,
    PaymentCreate,
    PaymentUpdate,
)


if getattr(sys, "frozen", False):
    exe_dir = Path(sys.executable).resolve().parent
    bundle_dir = Path(getattr(sys, "_MEIPASS", exe_dir))
else:
    bundle_dir = Path(__file__).resolve().parent.parent

DATA_DIR = APP_STORAGE_ROOT


def _resolve_static_dir() -> Path:
    candidates = [
        bundle_dir / "static",
        Path(sys.executable).resolve().parent / "_internal" / "static" if getattr(sys, "frozen", False) else bundle_dir / "static",
        Path(sys.executable).resolve().parent / "static" if getattr(sys, "frozen", False) else bundle_dir / "static",
    ]
    for c in candidates:
        if c.exists() and (c / "index.html").exists():
            return c
    return candidates[0]


STATIC_DIR = _resolve_static_dir()
DOCS_DIR = DATA_DIR / "uploads"
OWNER_HANDLER_NAME = "Primary operator"
# "Received from" when the applicant pays you directly (no recruiting partners).
RECEIPT_FROM_CLIENT = "Client"
RECEIPT_TYPES = frozenset({"advance", "after_work_permit", "after_visa", "uncertain"})
RECRUITER_PAID_VIA = frozenset({"Polish Bank Card", "BLIK", "Indian transfer", "Indian Card"})
PROFIT_SPLIT_MODES = frozenset({"equal", "custom_pct"})

app = FastAPI(title="Globaris Consulting CRM", version="1.0.0")


def add_activity(conn, kind: str, ref_id: Optional[int], message: str, icon: str = "📋", color: str = "var(--accent)"):
    conn.execute(
        """
        INSERT INTO activities (kind, ref_id, message, icon, color, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (kind, ref_id, message, icon, color),
    )


def error_payload(message: str, code: int):
    return {"error": message, "code": code}


def _normalize_email(value) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def _get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return str(row.get("value") or default)


def _resolve_backup_dir(conn) -> Path:
    configured = _get_setting(conn, "backup_dir", r"S:\CMS Backup").strip()
    if not configured:
        configured = r"S:\CMS Backup"
    return Path(configured)


def _resolve_writable_backup_dir(conn) -> Path:
    preferred = _resolve_backup_dir(conn)
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except Exception:
        fallback = DATA_DIR / "backups"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _set_setting(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _compute_backup_signature(conn) -> str:
    data = {}
    for table in (
        "clients",
        "tasks",
        "payments",
        "documents",
        "client_folder_paths",
        "deals",
        "deal_partners",
        "deal_partner_receipts",
        "deal_recruiter_payments",
        "deal_transactions",
    ):
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {c["name"] for c in cols}
        has_id = "id" in names
        has_updated = "updated_at" in names
        has_created = "created_at" in names
        ts_expr = "''"
        if has_updated and has_created:
            ts_expr = "COALESCE(updated_at, created_at, '')"
        elif has_updated:
            ts_expr = "COALESCE(updated_at, '')"
        elif has_created:
            ts_expr = "COALESCE(created_at, '')"
        id_expr = "0"
        if has_id:
            id_expr = "COALESCE(SUM(id), 0)"

        if "is_deleted" in names:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS c,
                    {id_expr} AS sid,
                    COALESCE(MAX({ts_expr}), '') AS mx
                FROM {table}
                WHERE is_deleted = 0
                """
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS c,
                    {id_expr} AS sid,
                    COALESCE(MAX({ts_expr}), '') AS mx
                FROM {table}
                """
            ).fetchone()
        data[table] = {"count": row["c"], "sum_id": row["sid"], "max_ts": row["mx"]}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    return digest


def _create_backup_files(conn, backup_dir: Path, versioned: bool = False) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    if versioned:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_path = backup_dir / f"immigration_crm_readable_{stamp}.xlsx"
        json_path = backup_dir / f"immigration_crm_restore_{stamp}.json"
    else:
        excel_path = backup_dir / "immigration_crm_readable_latest.xlsx"
        json_path = backup_dir / "immigration_crm_restore_latest.json"

    write_readable_excel(excel_path, conn)
    payload = build_full_backup_payload(conn)
    json_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return {
        "excel_path": str(excel_path),
        "json_path": str(json_path),
        "csv_path": str(excel_path),
    }


def _maybe_create_change_backup(conn, force: bool = False, versioned: bool = True) -> dict:
    try:
        current_sig = _compute_backup_signature(conn)
        last_sig = _get_setting(conn, "backup_last_signature", "")
        if not force and current_sig == last_sig:
            return {"created": False, "reason": "no_change"}
        backup_dir = _resolve_writable_backup_dir(conn)
        result = _create_backup_files(conn, backup_dir, versioned=versioned)
        _set_setting(conn, "backup_last_signature", current_sig)
        _set_setting(conn, "backup_last_at", datetime.now().isoformat())
        _set_setting(conn, "backup_last_error", "")
        _set_setting(conn, "backup_last_dir_used", str(backup_dir))
        return {"created": True, **result, "backup_dir_used": str(backup_dir)}
    except Exception as exc:
        # Backup must never block business operations.
        _set_setting(conn, "backup_last_error", str(exc))
        return {"created": False, "reason": "backup_error", "error": str(exc)}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=error_payload(str(exc.detail), exc.status_code))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=error_payload("Validation error", 422) | {"details": exc.errors()})


@app.on_event("startup")
def startup_event():
    init_db()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    return RedirectResponse(url="/static/index.html", status_code=307)


@app.get("/clients/{client_id}")
def serve_client_view(client_id: int):
    return RedirectResponse(url="/static/index.html", status_code=307)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/dashboard/stats")
def dashboard_stats():
    with get_db() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS total_clients,
                SUM(CASE WHEN stage = 'Documentation Done' THEN 1 ELSE 0 END) AS documentation_done,
                SUM(CASE WHEN stage = 'Permit Under Process' THEN 1 ELSE 0 END) AS permit_under_process,
                SUM(CASE WHEN stage = 'Permit Approved' THEN 1 ELSE 0 END) AS permit_approved,
                SUM(CASE WHEN stage = 'Waiting for Visa Decision' THEN 1 ELSE 0 END) AS waiting_for_visa_decision
            FROM clients
            WHERE is_deleted = 0
            """
        ).fetchone()

        stage_rows = conn.execute(
            """
            SELECT stage, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0
            GROUP BY stage
            ORDER BY count DESC
            """
        ).fetchall()

        country_rows = conn.execute(
            """
            SELECT country, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0
            GROUP BY country
            ORDER BY count DESC
            LIMIT 8
            """
        ).fetchall()

    return {
        "totals": totals,
        "stage_breakdown": stage_rows,
        "country_breakdown": country_rows,
    }


@app.get("/api/dashboard/overview")
def dashboard_overview():
    with get_db() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS total_clients,
                SUM(CASE WHEN stage = 'Permit Under Process' THEN 1 ELSE 0 END) AS under_process,
                SUM(CASE WHEN stage = 'Permit Approved' THEN 1 ELSE 0 END) AS approved_count,
                SUM(CASE WHEN stage = 'Waiting for Visa Decision' THEN 1 ELSE 0 END) AS waiting_count
            FROM clients
            WHERE is_deleted = 0
            """
        ).fetchone()

        stage_breakdown = conn.execute(
            """
            SELECT stage, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0
            GROUP BY stage
            ORDER BY count DESC
            """
        ).fetchall()

        recent_clients = conn.execute(
            """
            SELECT id, full_name, phone, country, visa_type, stage, created_at
            FROM clients
            WHERE is_deleted = 0
            ORDER BY datetime(created_at) DESC
            LIMIT 7
            """
        ).fetchall()

        monthly_clients = conn.execute(
            """
            SELECT strftime('%Y-%m', created_at) AS ym, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0
            GROUP BY ym
            """
        ).fetchall()

    now = datetime.now()
    months = []
    for idx in range(5, -1, -1):
        pivot = now.replace(day=1) - timedelta(days=idx * 30)
        ym = pivot.strftime("%Y-%m")
        label = pivot.strftime("%b")
        months.append((ym, label))

    by_month = {row["ym"]: row["count"] for row in monthly_clients if row["ym"]}
    trend = [{"month": label, "count": by_month.get(ym, 0)} for ym, label in months]

    activities = [
        {
            "title": "Client added",
            "subtitle": f"{rc['full_name']}",
            "time": rc["created_at"],
        }
        for rc in recent_clients[:3]
    ]

    return {
        "totals": totals,
        "stage_breakdown": stage_breakdown,
        "trend": trend,
        "recent_clients": recent_clients,
        "recent_activities": activities,
    }


@app.get("/api/dashboard")
def dashboard():
    with get_db() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS total_cases,
                SUM(CASE WHEN stage IN ('Documentation Done', 'Permit Under Process', 'Waiting for Visa Decision') THEN 1 ELSE 0 END) AS active_cases,
                SUM(CASE WHEN stage = 'Permit Under Process' THEN 1 ELSE 0 END) AS processing_cases,
                SUM(CASE WHEN stage = 'Permit Approved' THEN 1 ELSE 0 END) AS approved_cases
            FROM clients
            WHERE is_deleted = 0
            """
        ).fetchone()
        finance = conn.execute(
            """
            SELECT
                (SELECT COALESCE(SUM(total_fee), 0) FROM clients WHERE is_deleted = 0) AS total_fee,
                (SELECT COALESCE(SUM(amount_paid), 0) FROM payments WHERE is_deleted = 0)
                + (
                    SELECT COALESCE(SUM(dpr.amount), 0)
                    FROM deal_partner_receipts dpr
                    WHERE dpr.received_from = ?
                ) AS total_paid
            """,
            (RECEIPT_FROM_CLIENT,),
        ).fetchone()
        stages = conn.execute(
            """
            SELECT stage, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0
            GROUP BY stage
            ORDER BY count DESC
            """
        ).fetchall()
        activities = conn.execute(
            """
            SELECT id, message, icon, color, created_at
            FROM activities
            ORDER BY datetime(created_at) DESC
            LIMIT 8
            """
        ).fetchall()
        analytics = _build_reports_analytics(conn)
        collections_6m = analytics["collections_by_month"][-6:]
        country_mix = conn.execute(
            """
            SELECT country, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0 AND country IS NOT NULL AND TRIM(country) != ''
            GROUP BY country
            ORDER BY count DESC
            LIMIT 8
            """
        ).fetchall()
        monthly_new = conn.execute(
            """
            SELECT strftime('%Y-%m', created_at) AS ym, COUNT(*) AS count
            FROM clients
            WHERE is_deleted = 0
            GROUP BY ym
            HAVING ym IS NOT NULL AND ym != ''
            ORDER BY ym
            """
        ).fetchall()
        ym_map = {r["ym"]: r["count"] for r in monthly_new}
        months_6 = _last_n_calendar_months_chrono(6)
        opened_series = [{"ym": ym, "label": lab, "count": int(ym_map.get(ym, 0))} for ym, lab in months_6]
        recent_cases = conn.execute(
            """
            SELECT id, full_name, stage, country, visa_type, updated_at
            FROM clients
            WHERE is_deleted = 0
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT 6
            """
        ).fetchall()

    revenue = float(finance["total_paid"])
    book = analytics["book"]
    return {
        "stats": {
            "total_cases": totals["total_cases"] or 0,
            "active_cases": totals["active_cases"] or 0,
            "processing_cases": totals["processing_cases"] or 0,
            "revenue": revenue,
            "approved_cases": int(totals["approved_cases"] or 0),
        },
        "pipeline": stages,
        "recent_activities": activities,
        "operational": {
            "country_mix": [{"country": r["country"], "count": r["count"]} for r in country_mix],
            "cases_opened_trend": opened_series,
            "recent_cases": [
                {
                    "id": r["id"],
                    "full_name": r["full_name"],
                    "stage": r["stage"],
                    "country": r["country"],
                    "visa_type": r["visa_type"],
                    "updated_at": r["updated_at"],
                }
                for r in recent_cases
            ],
        },
        "finance": {
            "total_fee": float(finance["total_fee"]),
            "total_paid": revenue,
            "outstanding": float(book["outstanding"]),
            "collections_trend": collections_6m,
            "top_outstanding": analytics["top_outstanding"][:6],
            "personal": analytics["personal"],
            "payment_modes": analytics["payment_modes"],
        },
    }


@app.get("/api/clients")
def list_clients(
    search: str = "",
    stage: Optional[str] = None,
    country: Optional[str] = None,
    visa_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=200),
):
    where = ["is_deleted = 0"]
    params = []
    if search:
        where.append("(full_name LIKE ? OR email LIKE ? OR phone LIKE ?)")
        term = f"%{search.strip()}%"
        params.extend([term, term, term])
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if country:
        where.append("country = ?")
        params.append(country)
    if visa_type:
        where.append("visa_type = ?")
        params.append(visa_type)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    offset = (page - 1) * limit

    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS count FROM clients {where_sql}",
            params,
        ).fetchone()["count"]
        rows = conn.execute(
            f"""
            SELECT *
            FROM clients
            {where_sql}
            ORDER BY datetime(created_at) DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()

    return {"items": rows, "page": page, "limit": limit, "total": total}


@app.get("/api/clients/search")
def search_clients(q: str = Query(default="")):
    term = f"%{q.strip()}%"
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM clients
            WHERE is_deleted = 0 AND (full_name LIKE ? OR email LIKE ? OR phone LIKE ? OR country LIKE ?)
            ORDER BY datetime(created_at) DESC
            """,
            (term, term, term, term),
        ).fetchall()
    return {"items": rows}


@app.get("/api/clients/filters")
def filter_options():
    with get_db() as conn:
        stages = conn.execute("SELECT DISTINCT stage FROM clients WHERE is_deleted = 0 ORDER BY stage").fetchall()
        countries = conn.execute(
            "SELECT DISTINCT country FROM clients WHERE is_deleted = 0 ORDER BY country"
        ).fetchall()
        visa_types = conn.execute(
            "SELECT DISTINCT visa_type FROM clients WHERE is_deleted = 0 ORDER BY visa_type"
        ).fetchall()
    return {
        "stages": [r["stage"] for r in stages],
        "countries": [r["country"] for r in countries],
        "visa_types": [r["visa_type"] for r in visa_types],
    }


@app.post("/api/clients")
def create_client(payload: ClientCreate):
    with get_db() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO clients
                (full_name, email, phone, country, visa_type, stage, nationality, passport, process_started_on, total_fee, notes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    payload.full_name,
                    _normalize_email(payload.email),
                    payload.phone,
                    payload.country,
                    payload.visa_type,
                    payload.stage,
                    payload.nationality,
                    payload.passport,
                    payload.process_started_on,
                    payload.total_fee,
                    payload.notes,
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed: clients.email" in str(exc):
                raise HTTPException(status_code=409, detail="Email already exists") from exc
            raise

        created = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        add_activity(conn, "client", created["id"], f"New case added: {created['full_name']}", "📁", "var(--accent)")
        _sync_deal_from_client_payload(conn, created["id"], payload, is_create=True)
        _maybe_create_change_backup(conn)
    return created


@app.get("/api/clients/{client_id}")
def get_client(client_id: int):
    with get_db() as conn:
        client = conn.execute("SELECT * FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

    return {"client": client}


@app.put("/api/clients/{client_id}")
def update_client(client_id: int, payload: ClientUpdate):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")

        try:
            conn.execute(
                """
                UPDATE clients
                SET full_name = ?, email = ?, phone = ?, country = ?, visa_type = ?, stage = ?,
                    nationality = ?, passport = ?, process_started_on = ?, total_fee = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload.full_name,
                    _normalize_email(payload.email),
                    payload.phone,
                    payload.country,
                    payload.visa_type,
                    payload.stage,
                    payload.nationality,
                    payload.passport,
                    payload.process_started_on,
                    payload.total_fee,
                    payload.notes,
                    client_id,
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed: clients.email" in str(exc):
                raise HTTPException(status_code=409, detail="Email already exists") from exc
            raise

        _sync_deal_from_client_payload(conn, client_id, payload, is_create=False)
        updated = conn.execute("SELECT * FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        changed = []
        for f in ("full_name", "email", "phone", "country", "visa_type", "stage", "nationality", "passport", "process_started_on", "notes"):
            if str(existing.get(f, "")) != str(updated.get(f, "")):
                changed.append(f)
        suffix = f" ({', '.join(changed)})" if changed else ""
        add_activity(conn, "client", client_id, f"Case updated: {updated['full_name']}{suffix}", "✏️", "var(--blue)")
        _maybe_create_change_backup(conn)
    return updated


@app.delete("/api/clients/{client_id}")
def delete_client(client_id: int):
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Client not found")
        c = conn.execute("SELECT full_name FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        conn.execute("UPDATE clients SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (client_id,))
        conn.execute("UPDATE tasks SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE client_id = ?", (client_id,))
        conn.execute("UPDATE payments SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE client_id = ?", (client_id,))
        add_activity(conn, "client", client_id, f"Case deleted: {c['full_name']}", "🗑", "var(--red)")
    compact_active_client_ids()
    with get_db() as conn:
        _maybe_create_change_backup(conn)
    return {"deleted": True}


@app.post("/api/clients/reindex-ids")
def reindex_client_ids():
    """Compact active case IDs to #1…#n (e.g. after deletes). Safe to call anytime."""
    result = compact_active_client_ids()
    with get_db() as conn:
        if result.get("changed"):
            add_activity(conn, "system", None, "Case IDs renumbered to stay sequential (#1…#n)", "↻", "var(--navy)")
        _maybe_create_change_backup(conn)
    return {
        "ok": True,
        "changed": bool(result.get("changed")),
        "active_count": result.get("active_count"),
    }


@app.get("/api/clients/export/csv")
def export_clients_csv():
    def gen():
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["id", "full_name", "email", "phone", "country", "visa_type", "stage", "notes", "created_at", "updated_at"])
        yield stream.getvalue()
        stream.seek(0)
        stream.truncate(0)
        with get_db() as conn:
            cursor = conn.execute(
                """
                SELECT id, full_name, email, phone, country, visa_type, stage, notes, created_at, updated_at
                FROM clients
                WHERE is_deleted = 0
                ORDER BY datetime(created_at) DESC
                """
            )
            for row in cursor:
                writer.writerow([row["id"], row["full_name"], row["email"], row["phone"], row["country"], row["visa_type"], row["stage"], row["notes"], row["created_at"], row["updated_at"]])
                yield stream.getvalue()
                stream.seek(0)
                stream.truncate(0)

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clients_export.csv"},
    )


@app.get("/api/payments")
def list_payments(client_id: Optional[int] = None):
    where = ["p.is_deleted = 0"]
    params = []
    if client_id is not None:
        where.append("p.client_id = ?")
        params.append(client_id)
    where_sql = f"WHERE {' AND '.join(where)}"
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT p.*, c.full_name AS client_name
            FROM payments p
            JOIN clients c ON c.id = p.client_id AND c.is_deleted = 0
            {where_sql}
            ORDER BY datetime(p.created_at) DESC
            """
            ,
            params,
        ).fetchall()
    return {"items": rows}


@app.post("/api/payments")
def create_payment(payload: PaymentCreate):
    with get_db() as conn:
        client = conn.execute("SELECT id FROM clients WHERE id = ? AND is_deleted = 0", (payload.client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        cursor = conn.execute(
            """
            INSERT INTO payments (client_id, description, amount_paid, payment_type, collected_by, paid_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                payload.client_id,
                payload.description,
                payload.amount_paid,
                payload.payment_type,
                payload.collected_by,
                payload.paid_at,
            ),
        )
        created = conn.execute("SELECT * FROM payments WHERE id = ?", (cursor.lastrowid,)).fetchone()
        add_activity(conn, "payment", created["id"], "Payment added", "💰", "var(--green)")
        _maybe_create_change_backup(conn)
    return created


@app.put("/api/payments/{payment_id}")
def update_payment(payment_id: int, payload: PaymentUpdate):
    with get_db() as conn:
        current = conn.execute("SELECT * FROM payments WHERE id = ? AND is_deleted = 0", (payment_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Payment not found")
        new_client_id = payload.client_id if payload.client_id is not None else current["client_id"]
        client = conn.execute("SELECT id FROM clients WHERE id = ? AND is_deleted = 0", (new_client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        conn.execute(
            """
            UPDATE payments
            SET client_id = ?, description = ?, amount_paid = ?, payment_type = ?, collected_by = ?, paid_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                new_client_id,
                payload.description if payload.description is not None else current["description"],
                payload.amount_paid if payload.amount_paid is not None else current["amount_paid"],
                payload.payment_type if payload.payment_type is not None else current.get("payment_type", "UPI"),
                payload.collected_by if payload.collected_by is not None else current.get("collected_by", ""),
                payload.paid_at if payload.paid_at is not None else current.get("paid_at"),
                payment_id,
            ),
        )
        updated = conn.execute("SELECT * FROM payments WHERE id = ? AND is_deleted = 0", (payment_id,)).fetchone()
        changed = []
        for f in ("client_id", "description", "amount_paid", "payment_type", "collected_by", "paid_at"):
            if str(current.get(f, "")) != str(updated.get(f, "")):
                changed.append(f)
        suffix = f" ({', '.join(changed)})" if changed else ""
        add_activity(conn, "payment", payment_id, f"Payment updated{suffix}", "💵", "var(--blue)")
        _maybe_create_change_backup(conn)
    return updated


@app.delete("/api/payments/{payment_id}")
def delete_payment(payment_id: int):
    with get_db() as conn:
        current = conn.execute("SELECT id FROM payments WHERE id = ? AND is_deleted = 0", (payment_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Payment not found")
        conn.execute("UPDATE payments SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payment_id,))
        add_activity(conn, "payment", payment_id, "Payment deleted", "🗑", "var(--red)")
        _maybe_create_change_backup(conn)
    return {"deleted": True}


@app.get("/api/activities")
def list_activities(limit: int = 50):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM activities
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {"items": rows}


def _parse_recruiting_partners(raw: str) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def _finance_handler_names(recruiting_partners_text: str) -> list[str]:
    partners = _parse_recruiting_partners(recruiting_partners_text or "")
    return [OWNER_HANDLER_NAME] + partners


def _effective_recruiter_fees_total(deal: dict) -> float:
    t = float(deal.get("recruiter_fees_total") or 0)
    if t > 0:
        return t
    return float(deal.get("recruiter_ref_advance") or 0) + float(deal.get("recruiter_ref_after_permit") or 0)


def _sum_client_direct_receipts_to_me(conn, client_id: int) -> float:
    """Section 3: money the Client paid directly to Me (no Indian partner), stored on deal_partner_receipts."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(dpr.amount), 0) AS s
        FROM deal_partner_receipts dpr
        INNER JOIN deals d ON d.id = dpr.deal_id
        WHERE d.client_id = ? AND dpr.received_from = ?
        """,
        (client_id, RECEIPT_FROM_CLIENT),
    ).fetchone()
    return float(row["s"] or 0) if row else 0.0


def _deal_money_collected(conn, client_id: int) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN is_deleted = 0 THEN amount_paid ELSE 0 END), 0) AS s
        FROM payments WHERE client_id = ?
        """,
        (client_id,),
    ).fetchone()
    payments_sum = float(row["s"] or 0) if row else 0.0
    return payments_sum + _sum_client_direct_receipts_to_me(conn, client_id)


def _my_share_of_profit(conn, deal: dict) -> float:
    profit = float(deal.get("profit_to_split") or 0)
    mode = str(deal.get("profit_split_mode") or "equal")
    if profit <= 0:
        return 0.0
    deal_id = deal["id"]
    if mode == "custom_pct":
        row = conn.execute(
            "SELECT share_value FROM deal_partners WHERE deal_id = ? AND name = ?",
            (deal_id, OWNER_HANDLER_NAME),
        ).fetchone()
        pct = float(row["share_value"] or 0) if row else 0.0
        return profit * (pct / 100.0)
    names = _finance_handler_names(deal.get("recruiting_partners") or "")
    n = len(names)
    if n <= 0:
        return 0.0
    return profit / n


def _deal_personal_taken_and_to_come(conn, deal: dict) -> tuple[float, float]:
    deal_id = deal["id"]
    rec_sum = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM deal_partner_receipts WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()
    pay_sum = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM deal_recruiter_payments WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()
    receipts_total = float(rec_sum["s"] or 0) if rec_sum else 0.0
    recruiter_total = float(pay_sum["s"] or 0) if pay_sum else 0.0
    personal_taken = receipts_total - recruiter_total
    credits_row = conn.execute(
        "SELECT COALESCE(SUM(share_settlement), 0) AS s FROM deal_partner_receipts WHERE deal_id = ?",
        (deal_id,),
    ).fetchone()
    credits = float(credits_row["s"] or 0) if credits_row else 0.0
    my_share = _my_share_of_profit(conn, deal)
    still = max(0.0, my_share - credits)
    return personal_taken, still


def _last_n_calendar_months_chrono(n: int) -> list[tuple[str, str]]:
    """Return list of (YYYY-MM, short label) oldest first."""
    end = datetime.now().replace(day=1)
    stack: list[tuple[str, str]] = []
    d = end
    for _ in range(n):
        stack.append((d.strftime("%Y-%m"), d.strftime("%b")))
        if d.month == 1:
            d = d.replace(year=d.year - 1, month=12)
        else:
            d = d.replace(month=d.month - 1)
    return list(reversed(stack))


def _per_client_finance_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            c.id AS client_id,
            c.full_name AS client_name,
            c.total_fee AS total_fee,
            COALESCE(SUM(CASE WHEN p.is_deleted = 0 THEN p.amount_paid ELSE 0 END), 0)
            + (
                SELECT COALESCE(SUM(dpr.amount), 0)
                FROM deals d
                INNER JOIN deal_partner_receipts dpr ON dpr.deal_id = d.id AND dpr.received_from = ?
                WHERE d.client_id = c.id
            ) AS total_paid
        FROM clients c
        LEFT JOIN payments p ON p.client_id = c.id
        WHERE c.is_deleted = 0
        GROUP BY c.id, c.full_name, c.total_fee
        """,
        (RECEIPT_FROM_CLIENT,),
    ).fetchall()
    out = []
    for r in rows:
        tf = float(r["total_fee"] or 0)
        tp = float(r["total_paid"] or 0)
        out.append(
            {
                "client_id": r["client_id"],
                "client_name": r["client_name"],
                "total_fee": tf,
                "total_paid": tp,
                "balance": tf - tp,
            }
        )
    return out


def _collections_by_month_raw(conn) -> dict[str, float]:
    merged: dict[str, float] = {}
    for row in conn.execute(
        """
        SELECT strftime('%Y-%m', COALESCE(paid_at, created_at)) AS ym,
               COALESCE(SUM(amount_paid), 0) AS amt
        FROM payments
        WHERE is_deleted = 0
        GROUP BY ym
        """
    ).fetchall():
        ym = row["ym"]
        if ym:
            merged[ym] = merged.get(ym, 0.0) + float(row["amt"] or 0)
    for row in conn.execute(
        """
        SELECT strftime('%Y-%m', COALESCE(received_at, created_at)) AS ym,
               COALESCE(SUM(amount), 0) AS amt
        FROM deal_partner_receipts
        WHERE received_from = ?
        GROUP BY ym
        """,
        (RECEIPT_FROM_CLIENT,),
    ).fetchall():
        ym = row["ym"]
        if ym:
            merged[ym] = merged.get(ym, 0.0) + float(row["amt"] or 0)
    return merged


def _personal_finance_aggregate(conn) -> tuple[float, float]:
    deals = conn.execute(
        """
        SELECT d.*
        FROM deals d
        JOIN clients c ON c.id = d.client_id
        WHERE c.is_deleted = 0
        """
    ).fetchall()
    total_taken = 0.0
    total_to_come = 0.0
    for deal in deals:
        taken, still = _deal_personal_taken_and_to_come(conn, deal)
        total_taken += taken
        total_to_come += still
    return total_taken, total_to_come


def _build_reports_analytics(conn) -> dict:
    rows = _per_client_finance_rows(conn)
    total_fee = sum(r["total_fee"] for r in rows)
    total_paid = sum(r["total_paid"] for r in rows)
    outstanding = total_fee - total_paid
    top_out = sorted([r for r in rows if r["balance"] > 0.01], key=lambda x: -x["balance"])[:12]
    months = _last_n_calendar_months_chrono(12)
    raw = _collections_by_month_raw(conn)
    collections = [{"ym": ym, "label": lab, "amount": round(raw.get(ym, 0.0), 2)} for ym, lab in months]
    taken, to_come = _personal_finance_aggregate(conn)
    modes = conn.execute(
        """
        SELECT payment_type, COALESCE(SUM(amount_paid), 0) AS amt
        FROM payments
        WHERE is_deleted = 0
        GROUP BY payment_type
        """
    ).fetchall()
    payment_modes = [
        {"payment_type": str(r["payment_type"] or "Other"), "amount": float(r["amt"] or 0)} for r in modes
    ]
    return {
        "book": {
            "total_fee": round(total_fee, 2),
            "total_collected": round(total_paid, 2),
            "outstanding": round(outstanding, 2),
        },
        "top_outstanding": top_out,
        "collections_by_month": collections,
        "personal": {"personal_taken": round(taken, 2), "still_to_come": round(to_come, 2)},
        "payment_modes": payment_modes,
    }


def _build_finance_breakdown(conn, client_id: int) -> dict:
    client = conn.execute(
        "SELECT * FROM clients WHERE id = ? AND is_deleted = 0",
        (client_id,),
    ).fetchone()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    deal = _ensure_deal(conn, client_id)
    collected = _deal_money_collected(conn, client_id)
    handlers = _finance_handler_names(deal.get("recruiting_partners") or "")
    partner_names_only = [h for h in handlers if h != OWNER_HANDLER_NAME]
    receipts = conn.execute(
        """
        SELECT * FROM deal_partner_receipts
        WHERE deal_id = ?
        ORDER BY COALESCE(received_at, created_at) DESC, id DESC
        """,
        (deal["id"],),
    ).fetchall()
    recruiter_rows = conn.execute(
        """
        SELECT * FROM deal_recruiter_payments
        WHERE deal_id = ?
        ORDER BY COALESCE(paid_at, created_at) DESC, id DESC
        """,
        (deal["id"],),
    ).fetchall()
    partner_payments = conn.execute(
        """
        SELECT * FROM payments
        WHERE client_id = ? AND is_deleted = 0
        ORDER BY COALESCE(paid_at, created_at) DESC, id DESC
        """,
        (client_id,),
    ).fetchall()
    pct_by_name: dict[str, float] = {}
    if str(deal.get("profit_split_mode") or "equal") == "custom_pct":
        for row in conn.execute(
            "SELECT name, share_value FROM deal_partners WHERE deal_id = ?",
            (deal["id"],),
        ).fetchall():
            pct_by_name[str(row["name"])] = float(row["share_value"] or 0)
    else:
        n = len(handlers)
        eq = 100.0 / n if n else 100.0
        for h in handlers:
            pct_by_name[h] = eq
    personal_taken, still_to_come = _deal_personal_taken_and_to_come(conn, deal)
    my_share_target = _my_share_of_profit(conn, deal)
    credits_total = sum(float(r.get("share_settlement") or 0) for r in receipts)
    return {
        "client_id": client_id,
        "client_name": client["full_name"],
        "total_client_fee": float(client.get("total_fee") or 0),
        "money_collected": collected,
        "recruiter_fees_total": _effective_recruiter_fees_total(deal),
        "deal": deal,
        "handler_names": handlers,
        "partner_names": partner_names_only,
        "handler_pcts": [{"name": n, "pct": round(pct_by_name.get(n, 0.0), 4)} for n in handlers],
        "client_partner_payments": partner_payments,
        "partner_receipts": receipts,
        "recruiter_payments": recruiter_rows,
        "computed": {
            "personal_taken": personal_taken,
            "money_still_to_come": still_to_come,
            "my_share_target": my_share_target,
            "share_settlement_credited": credits_total,
            "sum_received_to_me": sum(float(r.get("amount") or 0) for r in receipts),
            "sum_paid_to_recruiter": sum(float(r.get("amount") or 0) for r in recruiter_rows),
        },
    }


@app.get("/api/finance/summary")
def finance_summary():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id AS client_id,
                c.full_name AS client_name,
                c.total_fee AS total_fee,
                COALESCE(SUM(CASE WHEN p.is_deleted = 0 THEN p.amount_paid ELSE 0 END), 0)
                + (
                    SELECT COALESCE(SUM(dpr.amount), 0)
                    FROM deals d
                    INNER JOIN deal_partner_receipts dpr ON dpr.deal_id = d.id AND dpr.received_from = ?
                    WHERE d.client_id = c.id
                ) AS total_paid
            FROM clients c
            LEFT JOIN payments p ON p.client_id = c.id
            WHERE c.is_deleted = 0
            GROUP BY c.id, c.full_name, c.total_fee
            ORDER BY datetime(c.created_at) DESC
            """,
            (RECEIPT_FROM_CLIENT,),
        ).fetchall()
    items = []
    for row in rows:
        total_fee = float(row["total_fee"] or 0)
        total_paid = float(row["total_paid"] or 0)
        balance = total_fee - total_paid
        status = "Pending"
        if total_paid == 0:
            status = "Pending"
        elif total_paid < total_fee:
            status = "Advance Collected"
        else:
            status = "Fully Paid"
        items.append(
            {
                "client_id": row["client_id"],
                "client_name": row["client_name"],
                "total_fee": total_fee,
                "total_paid": total_paid,
                "balance": balance,
                "status": status,
            }
        )
    return {"items": items}


@app.get("/api/finance/personal-summary")
def finance_personal_summary():
    with get_db() as conn:
        deals = conn.execute(
            """
            SELECT d.*
            FROM deals d
            JOIN clients c ON c.id = d.client_id
            WHERE c.is_deleted = 0
            """
        ).fetchall()
        total_taken = 0.0
        total_to_come = 0.0
        for deal in deals:
            taken, still = _deal_personal_taken_and_to_come(conn, deal)
            total_taken += taken
            total_to_come += still
    return {"personal_taken": total_taken, "personal_to_come": total_to_come}


@app.get("/api/reports/analytics")
def reports_analytics():
    with get_db() as conn:
        return _build_reports_analytics(conn)


@app.get("/api/finance/breakdown/{client_id}")
def get_finance_breakdown(client_id: int):
    with get_db() as conn:
        return _build_finance_breakdown(conn, client_id)


def _sync_breakdown_payments(conn, client_id: int, partner_only: list[str], rows: list) -> None:
    allowed_pt = {"Cash", "UPI", "Bank Transfer"}
    existing = conn.execute(
        "SELECT id FROM payments WHERE client_id = ? AND is_deleted = 0",
        (client_id,),
    ).fetchall()
    keep: set[int] = set()
    for p in rows:
        amt = float(p.get("amount_paid") or 0)
        if amt <= 0:
            continue
        ptype = str(p.get("payment_type") or "UPI").strip()
        if ptype not in allowed_pt:
            raise HTTPException(status_code=400, detail="Payment mode must be Cash, UPI, or Bank Transfer")
        coll = str(p.get("collected_by") or "").strip()
        if coll not in partner_only:
            raise HTTPException(
                status_code=400,
                detail="Received by must be one of the Recruiting partners",
            )
        desc = str(p.get("description") or "").strip()
        paid_at = p.get("paid_at")
        pid = p.get("id")
        if pid:
            cur = conn.execute(
                """
                UPDATE payments
                SET amount_paid = ?, payment_type = ?, collected_by = ?, paid_at = ?, description = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND client_id = ? AND is_deleted = 0
                """,
                (amt, ptype, coll, paid_at, desc, int(pid), client_id),
            )
            if cur.rowcount:
                keep.add(int(pid))
        else:
            cur = conn.execute(
                """
                INSERT INTO payments (client_id, description, amount_paid, payment_type, collected_by, paid_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (client_id, desc, amt, ptype, coll, paid_at),
            )
            keep.add(int(cur.lastrowid))
    for row in existing:
        rid = int(row["id"])
        if rid not in keep:
            conn.execute(
                "UPDATE payments SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (rid,),
            )


@app.put("/api/finance/breakdown/{client_id}")
def put_finance_breakdown(client_id: int, payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")
    with get_db() as conn:
        client = conn.execute(
            "SELECT * FROM clients WHERE id = ? AND is_deleted = 0",
            (client_id,),
        ).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        deal = _ensure_deal(conn, client_id)
        mode = str(payload.get("profit_split_mode") or deal.get("profit_split_mode") or "equal")
        if mode not in PROFIT_SPLIT_MODES:
            raise HTTPException(status_code=400, detail="Invalid Final Profit split criteria")
        profit_to_split = float(payload.get("profit_to_split") or 0)
        recruiter_fees_total = float(payload.get("recruiter_fees_total") if payload.get("recruiter_fees_total") is not None else _effective_recruiter_fees_total(deal) or 0)

        if "total_client_fee" in payload:
            conn.execute(
                """
                UPDATE clients SET total_fee = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND is_deleted = 0
                """,
                (float(payload.get("total_client_fee") or 0), client_id),
            )

        handlers = _finance_handler_names(deal.get("recruiting_partners") or "")
        partner_only = [h for h in handlers if h != OWNER_HANDLER_NAME]

        if partner_only:
            _sync_breakdown_payments(conn, client_id, partner_only, payload.get("client_partner_payments") or [])

        conn.execute(
            """
            UPDATE deals
            SET profit_to_split = ?, profit_split_mode = ?, recruiter_fees_total = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (profit_to_split, mode, recruiter_fees_total, deal["id"]),
        )
        deal = conn.execute("SELECT * FROM deals WHERE id = ?", (deal["id"],)).fetchone()

        conn.execute("DELETE FROM deal_partners WHERE deal_id = ?", (deal["id"],))
        if mode == "custom_pct":
            hrows = payload.get("handler_pcts") or []
            pmap = {str(x.get("name", "")).strip(): float(x.get("pct") or 0) for x in hrows}
            total_pct = sum(pmap.get(h, 0.0) for h in handlers)
            if handlers and abs(total_pct - 100.0) > 0.05:
                raise HTTPException(status_code=400, detail="Custom percentages must sum to 100% across all handler names")
            for h in handlers:
                conn.execute(
                    """
                    INSERT INTO deal_partners (deal_id, name, share_type, share_value)
                    VALUES (?, ?, 'percentage', ?)
                    """,
                    (deal["id"], h, float(pmap.get(h, 0) or 0)),
                )

        conn.execute("DELETE FROM deal_partner_receipts WHERE deal_id = ?", (deal["id"],))
        for r in payload.get("partner_receipts") or []:
            amt = float(r.get("amount") or 0)
            rfrom = str(r.get("received_from") or "").strip()
            rtype = str(r.get("receipt_type") or "advance").strip()
            if rtype not in RECEIPT_TYPES:
                raise HTTPException(status_code=400, detail="Invalid Type on Money transferred to me")
            allowed_from = {RECEIPT_FROM_CLIENT, *partner_only}
            if amt > 0 and rfrom not in allowed_from:
                raise HTTPException(
                    status_code=400,
                    detail="Choose Received from: Client (personal case) or a Partner from Recruiting partners",
                )
            settlement = float(r.get("share_settlement") or 0)
            if settlement < 0 or settlement > amt:
                raise HTTPException(status_code=400, detail="Count toward my share settlement must be between 0 and Received amount")
            conn.execute(
                """
                INSERT INTO deal_partner_receipts
                (deal_id, amount, received_in, received_from, receipt_type, received_at, share_settlement)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deal["id"],
                    amt,
                    str(r.get("received_in") or "").strip(),
                    rfrom,
                    rtype,
                    r.get("received_at"),
                    settlement,
                ),
            )

        conn.execute("DELETE FROM deal_recruiter_payments WHERE deal_id = ?", (deal["id"],))
        for r in payload.get("recruiter_payments") or []:
            amt = float(r.get("amount") or 0)
            via = str(r.get("paid_via") or "").strip()
            if via not in RECRUITER_PAID_VIA:
                raise HTTPException(status_code=400, detail="Invalid Paid via for Recruiter payment")
            conn.execute(
                """
                INSERT INTO deal_recruiter_payments (deal_id, amount, paid_via, paid_at)
                VALUES (?, ?, ?, ?)
                """,
                (deal["id"], amt, via, r.get("paid_at")),
            )

        add_activity(conn, "finance", client_id, f"Finance Breakdown saved: {client['full_name']}", "🧮", "var(--cyan)")
        _maybe_create_change_backup(conn)
        return _build_finance_breakdown(conn, client_id)


def _sync_deal_from_client_payload(conn, client_id: int, payload: ClientCreate | ClientUpdate, *, is_create: bool = False):
    deal = _ensure_deal(conn, client_id)
    rp = str(payload.recruiting_partners or "").strip()
    conn.execute(
        """
        UPDATE deals
        SET recruiting_partners = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (rp, deal["id"]),
    )


def _ensure_deal(conn, client_id: int):
    row = conn.execute("SELECT * FROM deals WHERE client_id = ?", (client_id,)).fetchone()
    if row:
        return row
    conn.execute(
        """
        INSERT INTO deals (client_id, deal_value, declared_costing, agency_advance_cost, agency_final_cost, advance_expected, post_visa_expected, status, updated_at)
        VALUES (?, 0, 0, 0, 0, 0, 0, 'draft', CURRENT_TIMESTAMP)
        """,
        (client_id,),
    )
    return conn.execute("SELECT * FROM deals WHERE client_id = ?", (client_id,)).fetchone()


def _deal_summary(conn, client_id: int):
    deal = _ensure_deal(conn, client_id)
    tx = conn.execute(
        """
        SELECT txn_type, amount, phase
        FROM deal_transactions
        WHERE deal_id = ?
        """,
        (deal["id"],),
    ).fetchall()
    partners = conn.execute(
        """
        SELECT id, name, share_type, share_value
        FROM deal_partners
        WHERE deal_id = ?
        ORDER BY id
        """,
        (deal["id"],),
    ).fetchall()
    by_type = {
        "client_collection": 0.0,
        "agency_payment": 0.0,
        "friend_payout": 0.0,
        "owner_withdrawal": 0.0,
        "adjustment": 0.0,
        "partner_remittance_in": 0.0,
    }
    for r in tx:
        t = r.get("txn_type")
        by_type[t] = by_type.get(t, 0.0) + float(r.get("amount") or 0)

    deal_value = float(deal.get("deal_value") or 0)
    declared_costing = float(deal.get("declared_costing") or 0)
    agency_total_cost = float(deal.get("agency_advance_cost") or 0) + float(deal.get("agency_final_cost") or 0)
    distributable = max(deal_value - declared_costing, 0)
    partner_count = len(partners)
    equal_share = distributable / partner_count if partner_count else 0

    partner_allocations = []
    for p in partners:
        if p["share_type"] == "equal":
            expected = equal_share
        elif p["share_type"] == "percentage":
            expected = distributable * (float(p["share_value"] or 0) / 100.0)
        else:
            expected = float(p["share_value"] or 0)
        partner_allocations.append(
            {
                "id": p["id"],
                "name": p["name"],
                "share_type": p["share_type"],
                "share_value": float(p["share_value"] or 0),
                "expected_amount": expected,
            }
        )

    return {
        "deal": deal,
        "totals": {
            "deal_value": deal_value,
            "declared_costing": declared_costing,
            "agency_total_cost": agency_total_cost,
            "client_collection": by_type["client_collection"],
            "agency_payment": by_type["agency_payment"],
            "friend_payout": by_type["friend_payout"],
            "owner_withdrawal": by_type["owner_withdrawal"],
            "adjustment": by_type["adjustment"],
            "partner_remittance_in": by_type["partner_remittance_in"],
            "net_collected_after_agency": by_type["client_collection"] - by_type["agency_payment"],
            "distributable_pool": distributable,
            "your_retained_margin": by_type["owner_withdrawal"] + max(by_type["client_collection"] - by_type["agency_payment"] - by_type["friend_payout"], 0),
        },
        "partners": partner_allocations,
    }


@app.get("/api/deals/{client_id}")
def get_deal(client_id: int):
    with get_db() as conn:
        client = conn.execute("SELECT id FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        deal = _ensure_deal(conn, client_id)
    return deal


@app.put("/api/deals/{client_id}")
def upsert_deal(client_id: int, payload: dict):
    with get_db() as conn:
        client = conn.execute("SELECT id, full_name FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        deal = _ensure_deal(conn, client_id)
        conn.execute(
            """
            UPDATE deals
            SET deal_value = ?, declared_costing = ?, agency_advance_cost = ?, agency_final_cost = ?, advance_expected = ?, post_visa_expected = ?, status = ?, personal_pocket_expected_total = ?, recruiting_partners = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                float(payload.get("deal_value", deal["deal_value"]) or 0),
                float(payload.get("declared_costing", deal["declared_costing"]) or 0),
                float(payload.get("agency_advance_cost", deal["agency_advance_cost"]) or 0),
                float(payload.get("agency_final_cost", deal["agency_final_cost"]) or 0),
                float(payload.get("advance_expected", deal["advance_expected"]) or 0),
                float(payload.get("post_visa_expected", deal["post_visa_expected"]) or 0),
                str(payload.get("status", deal["status"] or "draft")),
                float(payload.get("personal_pocket_expected_total", deal.get("personal_pocket_expected_total") or 0) or 0),
                str(payload.get("recruiting_partners", deal.get("recruiting_partners") or "") or ""),
                deal["id"],
            ),
        )
        add_activity(conn, "finance", client_id, f"Deal accounting updated: {client['full_name']}", "🧮", "var(--cyan)")
        _maybe_create_change_backup(conn)
        updated = conn.execute("SELECT * FROM deals WHERE id = ?", (deal["id"],)).fetchone()
    return updated


@app.get("/api/deals/{client_id}/transactions")
def list_deal_transactions(client_id: int):
    with get_db() as conn:
        deal = _ensure_deal(conn, client_id)
        rows = conn.execute(
            """
            SELECT * FROM deal_transactions
            WHERE deal_id = ?
            ORDER BY COALESCE(txn_at, created_at) DESC, id DESC
            """,
            (deal["id"],),
        ).fetchall()
    return {"items": rows}


@app.post("/api/deals/{client_id}/transactions")
def add_deal_transaction(client_id: int, payload: dict):
    with get_db() as conn:
        client = conn.execute("SELECT id, full_name FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        deal = _ensure_deal(conn, client_id)
        txn_type = str(payload.get("txn_type") or "").strip()
        allowable = {"client_collection", "agency_payment", "friend_payout", "owner_withdrawal", "adjustment", "partner_remittance_in"}
        if txn_type not in allowable:
            raise HTTPException(status_code=400, detail="Invalid transaction type")
        amount = float(payload.get("amount") or 0)
        personal_portion = float(payload.get("personal_portion") or 0)
        if personal_portion < 0 or personal_portion > amount:
            raise HTTPException(status_code=400, detail="personal_portion must be between 0 and amount")
        conn.execute(
            """
            INSERT INTO deal_transactions (deal_id, txn_type, phase, amount, counterparty, payment_mode, notes, txn_at, personal_portion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deal["id"],
                txn_type,
                str(payload.get("phase") or "general"),
                amount,
                str(payload.get("counterparty") or ""),
                str(payload.get("payment_mode") or "Cash"),
                str(payload.get("notes") or ""),
                payload.get("txn_at"),
                personal_portion,
            ),
        )
        add_activity(conn, "finance", client_id, f"Deal transaction added: {client['full_name']}", "💸", "var(--green)")
        _maybe_create_change_backup(conn)
    return {"created": True}


@app.get("/api/deals/{client_id}/partners")
def list_deal_partners(client_id: int):
    with get_db() as conn:
        deal = _ensure_deal(conn, client_id)
        rows = conn.execute(
            """
            SELECT id, name, share_type, share_value
            FROM deal_partners
            WHERE deal_id = ?
            ORDER BY id
            """,
            (deal["id"],),
        ).fetchall()
    return {"items": rows}


@app.post("/api/deals/{client_id}/partners")
def add_or_update_deal_partner(client_id: int, payload: dict):
    with get_db() as conn:
        client = conn.execute("SELECT id, full_name FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        deal = _ensure_deal(conn, client_id)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Partner name is required")
        share_type = str(payload.get("share_type") or "equal")
        if share_type not in {"equal", "percentage", "fixed"}:
            raise HTTPException(status_code=400, detail="Invalid share type")
        share_value = float(payload.get("share_value") or 0)
        conn.execute(
            """
            INSERT INTO deal_partners (deal_id, name, share_type, share_value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(deal_id, name) DO UPDATE SET share_type = excluded.share_type, share_value = excluded.share_value
            """,
            (deal["id"], name, share_type, share_value),
        )
        add_activity(conn, "finance", client_id, f"Partner share updated: {client['full_name']}", "🤝", "var(--accent)")
        _maybe_create_change_backup(conn)
    return {"saved": True}


@app.get("/api/deals/{client_id}/summary")
def deal_summary(client_id: int):
    with get_db() as conn:
        client = conn.execute("SELECT id FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        summary = _deal_summary(conn, client_id)
    return summary


@app.get("/api/clients/{client_id}/detail")
def client_detail(client_id: int):
    with get_db() as conn:
        client = conn.execute(
            "SELECT * FROM clients WHERE id = ? AND is_deleted = 0",
            (client_id,),
        ).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        payments = conn.execute(
            """
            SELECT *
            FROM payments
            WHERE client_id = ? AND is_deleted = 0
            ORDER BY COALESCE(paid_at, created_at) DESC
            """,
            (client_id,),
        ).fetchall()
        docs = conn.execute(
            """
            SELECT id, original_name, content_type, file_size, created_at
            FROM documents
            WHERE client_id = ? AND is_deleted = 0
            ORDER BY datetime(created_at) DESC
            """,
            (client_id,),
        ).fetchall()
        direct_client_paid = _sum_client_direct_receipts_to_me(conn, client_id)
    total_fee = float(client.get("total_fee", 0))
    total_paid = sum(float(p.get("amount_paid", 0)) for p in payments) + direct_client_paid
    balance = total_fee - total_paid
    status = "Pending" if total_paid == 0 else ("Advance Collected" if total_paid < total_fee else "Fully Paid")
    return {
        "client": client,
        "documents": docs,
        "finance": {
            "total_fee": total_fee,
            "total_paid": total_paid,
            "balance": balance,
            "status": status,
            "transactions": payments,
        },
    }


@app.get("/api/settings")
def get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


@app.put("/api/settings")
def update_settings(payload: dict):
    with get_db() as conn:
        for key in ("biz_name", "backup_dir"):
            if key in payload:
                _set_setting(conn, key, str(payload[key]))
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('currency', '₹') ON CONFLICT(key) DO UPDATE SET value = '₹'"
        )
        _maybe_create_change_backup(conn)
    return {"updated": True}


@app.post("/api/backup/create")
def create_backup_now():
    with get_db() as conn:
        # Manual click should always create an explicit snapshot.
        result = _maybe_create_change_backup(conn, force=True, versioned=True)
    return result


@app.get("/api/documents")
def list_documents(client_id: Optional[int] = None):
    where = ["d.is_deleted = 0"]
    params = []
    if client_id is not None:
        where.append("d.client_id = ?")
        params.append(client_id)
    where_sql = f"WHERE {' AND '.join(where)}"
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT d.*, c.full_name AS client_name
            FROM documents d
            JOIN clients c ON c.id = d.client_id AND c.is_deleted = 0
            {where_sql}
            ORDER BY datetime(d.created_at) DESC
            """,
            params,
        ).fetchall()
    return {"items": rows}


@app.post("/api/documents")
async def upload_document(client_id: int = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    with get_db() as conn:
        client = conn.execute("SELECT id, full_name FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        stored_name = f"{uuid.uuid4().hex}_{file.filename}"
        path = DOCS_DIR / stored_name
        path.write_bytes(data)
        cursor = conn.execute(
            """
            INSERT INTO documents (client_id, original_name, stored_name, content_type, file_size)
            VALUES (?, ?, ?, ?, ?)
            """,
            (client_id, file.filename, stored_name, file.content_type or "application/octet-stream", len(data)),
        )
        add_activity(conn, "document", cursor.lastrowid, f"Document uploaded for {client['full_name']}: {file.filename}", "📄", "var(--cyan)")
        _maybe_create_change_backup(conn)
    return {"uploaded": True}


@app.get("/api/documents/{document_id}/download")
def download_document(document_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND is_deleted = 0",
            (document_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
    path = DOCS_DIR / row["stored_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Document file missing")
    return FileResponse(path, media_type=row.get("content_type") or "application/octet-stream", filename=row["original_name"])


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ? AND is_deleted = 0", (document_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        conn.execute("UPDATE documents SET is_deleted = 1 WHERE id = ?", (document_id,))
        add_activity(conn, "document", document_id, f"Document deleted: {row['original_name']}", "🗑", "var(--red)")
        _maybe_create_change_backup(conn)
    return {"deleted": True}


@app.get("/api/client-folders")
def list_client_folders():
    with get_db() as conn:
        rows = conn.execute("SELECT client_id, folder_path, updated_at FROM client_folder_paths ORDER BY client_id").fetchall()
    return {"items": rows}


@app.put("/api/clients/{client_id}/folder-path")
def set_client_folder_path(client_id: int, payload: dict):
    folder_path = (payload.get("folder_path") or "").strip()
    if not folder_path:
        raise HTTPException(status_code=400, detail="folder_path is required")
    with get_db() as conn:
        client = conn.execute("SELECT id, full_name FROM clients WHERE id = ? AND is_deleted = 0", (client_id,)).fetchone()
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        conn.execute(
            """
            INSERT INTO client_folder_paths (client_id, folder_path, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(client_id) DO UPDATE SET folder_path = excluded.folder_path, updated_at = CURRENT_TIMESTAMP
            """,
            (client_id, folder_path),
        )
        add_activity(conn, "document", client_id, f"Folder path updated for {client['full_name']}", "📁", "var(--blue)")
        _maybe_create_change_backup(conn)
    return {"updated": True}


@app.get("/api/clients/{client_id}/folder-files")
def list_folder_files(client_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT folder_path FROM client_folder_paths WHERE client_id = ?", (client_id,)).fetchone()
    if not row:
        return {"folder_path": "", "files": []}
    folder = Path(row["folder_path"])
    if not folder.exists() or not folder.is_dir():
        return {"folder_path": str(folder), "files": []}
    files = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if item.is_file():
            files.append({"name": item.name, "size": item.stat().st_size, "modified_at": datetime.fromtimestamp(item.stat().st_mtime).isoformat()})
    return {"folder_path": str(folder), "files": files}


@app.post("/api/clients/{client_id}/folder-files")
async def upload_folder_file(client_id: int, file: UploadFile = File(...)):
    with get_db() as conn:
        row = conn.execute("SELECT folder_path FROM client_folder_paths WHERE client_id = ?", (client_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Folder path not set for this client")
    folder = Path(row["folder_path"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / file.filename
    data = await file.read()
    target.write_bytes(data)
    return {"uploaded": True}


@app.get("/api/clients/{client_id}/folder-files/download")
def download_folder_file(client_id: int, name: str):
    safe_name = os.path.basename(name)
    with get_db() as conn:
        row = conn.execute("SELECT folder_path FROM client_folder_paths WHERE client_id = ?", (client_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Folder path not set for this client")
    file_path = Path(row["folder_path"]) / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return FileResponse(file_path, media_type=media_type, filename=safe_name)


@app.post("/api/clients/{client_id}/open-folder")
def open_folder_in_explorer(client_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT folder_path FROM client_folder_paths WHERE client_id = ?", (client_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Folder path not set for this client")
    folder = Path(row["folder_path"])
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder path not found")
    os.startfile(str(folder))
    return {"opened": True}


@app.get("/api/export/json")
def export_json():
    with get_db() as conn:
        payload = build_full_backup_payload(conn)
    blob = json.dumps(payload, ensure_ascii=True, indent=2)
    return StreamingResponse(
        iter([blob]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=immigration_crm_backup.json"},
    )


@app.get("/api/export/csv")
def export_full_csv():
    def gen():
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["id", "full_name", "email", "phone", "country", "visa_type", "stage", "total_fee", "amount_paid", "task_count"])
        yield stream.getvalue()
        stream.seek(0)
        stream.truncate(0)
        with get_db() as conn:
            cursor = conn.execute(
                """
                SELECT
                    c.id,
                    c.full_name,
                    c.email,
                    c.phone,
                    c.country,
                    c.visa_type,
                    c.stage,
                    c.total_fee AS total_fee,
                    COALESCE(SUM(CASE WHEN p.is_deleted = 0 THEN p.amount_paid ELSE 0 END), 0)
                    + (
                        SELECT COALESCE(SUM(dpr.amount), 0)
                        FROM deals d
                        INNER JOIN deal_partner_receipts dpr ON dpr.deal_id = d.id AND dpr.received_from = ?
                        WHERE d.client_id = c.id
                    ) AS amount_paid,
                    COUNT(DISTINCT CASE WHEN t.is_deleted = 0 THEN t.id END) AS task_count
                FROM clients c
                LEFT JOIN payments p ON p.client_id = c.id
                LEFT JOIN tasks t ON t.client_id = c.id
                WHERE c.is_deleted = 0
                GROUP BY c.id, c.full_name, c.email, c.phone, c.country, c.visa_type, c.stage
                ORDER BY c.id DESC
                """,
                (RECEIPT_FROM_CLIENT,),
            )
            for r in cursor:
                writer.writerow([r["id"], r["full_name"], r["email"], r["phone"], r["country"], r["visa_type"], r["stage"], f"₹{r['total_fee']}", f"₹{r['amount_paid']}", r["task_count"]])
                yield stream.getvalue()
                stream.seek(0)
                stream.truncate(0)
    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=immigration_crm_export.csv"},
    )


def _normalize_import_clients(clients: list) -> None:
    """Ensure required NOT NULL columns and unique email so SQLite insert succeeds."""
    now = datetime.now().isoformat()
    used_emails: set[str] = set()
    for c in clients:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "?")
        name = (str(c.get("full_name") or "").strip()) or f"Client #{cid}"
        c["full_name"] = name
        em = _normalize_email(c.get("email"))
        if not em:
            em = f"import_client_{cid}@placeholder.local"
        local, sep, domain = em.partition("@")
        if not sep:
            local, domain = em, "placeholder.local"
        candidate = em
        n = 1
        while candidate.lower() in used_emails:
            candidate = f"{local}_imp{n}@{domain}"
            n += 1
        used_emails.add(candidate.lower())
        c["email"] = candidate
        if c.get("phone") is None or str(c.get("phone")).strip() == "":
            c["phone"] = "—"
        if c.get("country") is None:
            c["country"] = ""
        if not (str(c.get("visa_type") or "").strip()):
            c["visa_type"] = "Full Time - Work Visa"
        if not (str(c.get("stage") or "").strip()):
            c["stage"] = "Documentation Done"
        if c.get("nationality") is None:
            c["nationality"] = ""
        if c.get("passport") is None:
            c["passport"] = ""
        if c.get("notes") is None:
            c["notes"] = ""
        if c.get("total_fee") is None:
            c["total_fee"] = 0
        if c.get("is_deleted") is None:
            c["is_deleted"] = 0
        if not c.get("created_at"):
            c["created_at"] = now
        if not c.get("updated_at"):
            c["updated_at"] = now


@app.post("/api/import/json")
async def import_json(request: Request):
    raw = await request.body()
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail="Empty file — choose a .json backup, not an Excel file.")
    try:
        text = raw.decode("utf-8-sig")
        payload = json.loads(text)
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"File is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Not valid JSON. Use “immigration_crm_restore_*.json” or Export from Settings (JSON backup), not the CSV/Excel file.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid backup: root must be a JSON object")
    if "clients" not in payload:
        raise HTTPException(status_code=400, detail="Invalid backup: missing “clients” array")

    ver = int(payload.get("backup_format_version") or 1)
    if ver < 2:
        for key in ("tasks", "payments"):
            if key not in payload:
                raise HTTPException(status_code=400, detail="Invalid backup: older format requires tasks and payments keys")
    else:
        for key in ("tasks", "payments"):
            if key not in payload:
                payload[key] = []

    clients = payload.get("clients") or []
    tasks = payload.get("tasks") or []
    payments = payload.get("payments") or []
    if not isinstance(clients, list) or not isinstance(tasks, list) or not isinstance(payments, list):
        raise HTTPException(status_code=400, detail="Invalid backup: clients, tasks, and payments must be arrays")

    _normalize_import_clients(clients)

    with get_db() as conn:
        existing_counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM clients WHERE is_deleted = 0) AS clients_count,
                (SELECT COUNT(*) FROM tasks WHERE is_deleted = 0) AS tasks_count,
                (SELECT COUNT(*) FROM payments WHERE is_deleted = 0) AS payments_count
            """
        ).fetchone()
        if (
            (existing_counts["clients_count"] or 0) > 0
            and len(clients) == 0
            and len(tasks) == 0
            and len(payments) == 0
        ):
            raise HTTPException(status_code=400, detail="Backup file appears empty; import aborted to prevent data loss")

        _maybe_create_change_backup(conn, force=True, versioned=True)

        try:
            restore_from_payload(conn, payload, _set_setting)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Import failed (data conflict). Try a full v2 JSON backup. SQLite: {exc}",
            ) from exc

        _maybe_create_change_backup(conn, force=True, versioned=True)
    return {"imported": True}


def run():
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)

