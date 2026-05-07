# Immigration Operations CRM

Desktop **immigration case and finance operations** app for Windows. It runs a small **local web server** and opens the UI in your browser (application window mode). Case and payment data stay **on your machine** unless you export backups.

**Version:** `1.0.6` — see [Version note](#version-note) below.

---

## What it does

- **Visa cases** — Applicants, contact details, destination country, visa type, pipeline stage, fees, and process dates.
- **Finance** — Client payments, balances, and a structured **finance breakdown** per case (operator, partners, recruiter, profit split, receipts).
- **Deals** — One financial deal per active client; partner shares, client receipts, recruiter-related payouts.
- **Dashboard & reports** — KPIs, collection trends, pipeline views, analytics widgets.
- **Documents & folders** — Optional paths to document folders and local file listing.
- **Settings** — Display name, currency symbol, **backup directory** for exports.
- **Backup & restore** — JSON full backup (in-app restore), CSV export, Excel-oriented backup where configured.

---

## Requirements

- **Windows 10 or 11**
- **Python 3.11+** (64-bit recommended), `python` on `PATH`
- **Microsoft Edge** or **Google Chrome** (app window for the UI)

---

## Quick start

1. Install Python from [python.org](https://www.python.org/downloads/) and enable **Add python.exe to PATH**.
2. Double-click **`Start Immigration CRM.bat`**.
   - First launch may install dependencies (`pip install -r requirements.txt`).
3. App URL: **`http://127.0.0.1:8765`** (browser should open automatically).
4. To stop: close the CRM window, or run **`Stop Immigration CRM.bat`** (frees port **8765**).

**Manual start:**

```bat
python -m pip install -r requirements.txt
python desktop_app.py
```

---

## Data storage

- **Database** (`immigration_crm.db`) is created in **this folder** when you run the Python app from here.
- **Browser profile** (embedded window): `%LOCALAPPDATA%\ImmigrationCRM\browser-profile` — not your CRM records.
- **Frozen / packaged builds** may store data under `%LOCALAPPDATA%\ImmigrationCRM\` (see `app/database.py`). A one-time copy from older app data folders may apply on first run if a legacy database is found.
- **Backups** — Set the folder in **Settings**. Use **Import backup JSON** only for files produced by this app’s backup/export.

---

## Configuration

- Default **backup path** in the UI is a placeholder: `C:\ImmigrationCRM\Backups` — set a real path.
- **Primary operator** in finance logic defaults to the neutral label **Primary operator** (adjust in code if needed).
- **Port 8765** is set in `desktop_app.py`; change there if required.

---

## Project layout

| Path | Purpose |
|------|---------|
| `desktop_app.py` | Uvicorn + opens UI |
| `app/main.py` | FastAPI API and logic |
| `app/database.py` | SQLite, schema |
| `app/backup_io.py` | Backup / restore / ID compaction |
| `app/schemas.py` | Request/response models |
| `static/` | Frontend |
| `requirements.txt` | Dependencies |
| `VERIFICATION_CHECKLIST.txt` | Release QA |

---

## Security & privacy

- Built for **local / trusted** use. No built-in multi-user authentication in this package.
- Do not expose **8765** to the internet without TLS, auth, and hardening.
- Treat JSON/CSV exports as **sensitive**.

---

## Troubleshooting

- **Pip install failed** — Run `python --version` in a new Command Prompt.
- **Port in use** — Run `Stop Immigration CRM.bat` or close the other instance.
- **Import JSON fails** — Use this app’s **JSON** backup, not CSV/Excel alone. UTF-8 (incl. BOM) supported.
- **Case IDs change after deletes** — Renumbering active cases to `#1…#n` after deletes is intentional.

---

## Verification

Use **`VERIFICATION_CHECKLIST.txt`**. Optional automated smoke tests can live as a small script in your dev tree (name it as you prefer).

---

## Version note

**1.0.6** is the current application version (`FastAPI` metadata in `app/main.py`). The patch number reflects **six major engineering passes** during active development (packaging, import/restore hardening, release QA and distribution, delete/ID compaction fixes, repository/GitHub prep, and branding-neutral open-source packaging). You can keep or replace this scheme when you release independently.

---

## License

See `LICENSE` in this repository.
