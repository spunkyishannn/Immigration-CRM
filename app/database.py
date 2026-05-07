import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path


def _resolve_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        la = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        primary = la / "ImmigrationCRM"
        primary.mkdir(parents=True, exist_ok=True)
        return primary
    return Path(__file__).resolve().parent.parent


BASE_DIR = _resolve_data_dir()
DB_PATH = BASE_DIR / "immigration_crm.db"


def _dict_factory(cursor: sqlite3.Cursor, row: tuple):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level="IMMEDIATE")
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL,
                country TEXT NOT NULL,
                visa_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                nationality TEXT DEFAULT '',
                passport TEXT DEFAULT '',
                process_started_on TEXT,
                total_fee REAL NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                title TEXT NOT NULL,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'Medium',
                category TEXT NOT NULL DEFAULT 'General',
                notes TEXT DEFAULT '',
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                description TEXT DEFAULT '',
                amount_paid REAL NOT NULL DEFAULT 0,
                payment_type TEXT NOT NULL DEFAULT 'UPI',
                collected_by TEXT DEFAULT '',
                paid_at TEXT,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                ref_id INTEGER,
                message TEXT NOT NULL,
                icon TEXT DEFAULT '📋',
                color TEXT DEFAULT 'var(--accent)',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_folder_paths (
                client_id INTEGER PRIMARY KEY,
                folder_path TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                content_type TEXT DEFAULT 'application/octet-stream',
                file_size INTEGER NOT NULL DEFAULT 0,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL UNIQUE,
                deal_value REAL NOT NULL DEFAULT 0,
                declared_costing REAL NOT NULL DEFAULT 0,
                agency_advance_cost REAL NOT NULL DEFAULT 0,
                agency_final_cost REAL NOT NULL DEFAULT 0,
                advance_expected REAL NOT NULL DEFAULT 0,
                post_visa_expected REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deal_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER NOT NULL,
                txn_type TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'general',
                amount REAL NOT NULL DEFAULT 0,
                counterparty TEXT DEFAULT '',
                payment_mode TEXT DEFAULT 'Cash',
                notes TEXT DEFAULT '',
                txn_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deal_id) REFERENCES deals(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deal_partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                share_type TEXT NOT NULL DEFAULT 'equal',
                share_value REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(deal_id, name),
                FOREIGN KEY(deal_id) REFERENCES deals(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deal_partner_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                received_in TEXT NOT NULL DEFAULT '',
                received_from TEXT NOT NULL DEFAULT '',
                receipt_type TEXT NOT NULL DEFAULT 'advance',
                received_at TEXT,
                share_settlement REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deal_id) REFERENCES deals(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deal_recruiter_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                paid_via TEXT NOT NULL DEFAULT '',
                paid_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deal_id) REFERENCES deals(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO app_settings (key, value) VALUES
            ('biz_name', 'Immigration CRM'),
            ('currency', '$');
            """
        )
        _ensure_column(conn, "clients", "nationality", "TEXT DEFAULT ''")
        _ensure_column(conn, "clients", "passport", "TEXT DEFAULT ''")
        _ensure_column(conn, "clients", "process_started_on", "TEXT")
        _ensure_column(conn, "clients", "total_fee", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "clients", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "clients", "deleted_at", "TEXT")
        _ensure_column(conn, "tasks", "priority", "TEXT NOT NULL DEFAULT 'Medium'")
        _ensure_column(conn, "tasks", "category", "TEXT NOT NULL DEFAULT 'General'")
        _ensure_column(conn, "tasks", "notes", "TEXT DEFAULT ''")
        _ensure_column(conn, "payments", "payment_type", "TEXT NOT NULL DEFAULT 'UPI'")
        _ensure_column(conn, "payments", "collected_by", "TEXT DEFAULT ''")
        _ensure_column(conn, "payments", "paid_at", "TEXT")
        conn.execute("UPDATE app_settings SET value = '₹' WHERE key = 'currency'")
        _ensure_column(conn, "tasks", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "tasks", "deleted_at", "TEXT")
        _ensure_column(conn, "payments", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "payments", "deleted_at", "TEXT")
        _ensure_column(conn, "documents", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "declared_costing", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "agency_advance_cost", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "agency_final_cost", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "advance_expected", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "post_visa_expected", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "status", "TEXT NOT NULL DEFAULT 'draft'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_search ON clients(full_name, email, phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_filters ON clients(stage, country, visa_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_client_status ON tasks(client_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_client ON payments(client_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_client ON deals(client_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_txn_deal ON deal_transactions(deal_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_partners_deal ON deal_partners(deal_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_partner_receipts_deal ON deal_partner_receipts(deal_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deal_recruiter_pay_deal ON deal_recruiter_payments(deal_id)")
        _ensure_column(conn, "deals", "personal_pocket_expected_total", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "recruiting_partners", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "deal_transactions", "personal_portion", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "profit_to_split", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "deals", "profit_split_mode", "TEXT NOT NULL DEFAULT 'equal'")
        _ensure_column(conn, "deals", "recruiter_ref_advance", "REAL NOT NULL DEFAULT 35000")
        _ensure_column(conn, "deals", "recruiter_ref_after_permit", "REAL NOT NULL DEFAULT 70000")
        _ensure_column(conn, "deals", "recruiter_fees_total", "REAL NOT NULL DEFAULT 0")
        conn.execute(
            """
            UPDATE deals
            SET recruiter_fees_total = COALESCE(recruiter_ref_advance, 0) + COALESCE(recruiter_ref_after_permit, 0)
            WHERE recruiter_fees_total = 0
              AND (COALESCE(recruiter_ref_advance, 0) > 0 OR COALESCE(recruiter_ref_after_permit, 0) > 0)
            """
        )
        conn.execute(
            "UPDATE app_settings SET value = 'Immigration CRM' WHERE key = 'biz_name' AND (value = '' OR value = 'Immigration CRM')"
        )


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str):
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {c["name"] for c in columns}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

