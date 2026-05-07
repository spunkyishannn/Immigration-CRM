# Globaris Consulting — Operations CRM

Desktop-friendly **immigration case and finance operations** app for Windows. It runs a small **local web server** and opens the UI in your browser (application window mode). All case and payment data stay **on your machine** unless you export backups yourself.

---

## What it does

- **Visa cases** — Track applicants with contact details, destination country, visa type, pipeline stage, fees, and process dates.
- **Finance** — Record client payments, balances, and a structured **finance breakdown** per case (operator, partners, recruiter, profit split, receipts).
- **Deals** — One financial deal per active client; supports partner shares, client receipts, and recruiter-related payouts.
- **Dashboard & reports** — Operational KPIs, collection trends, pipeline views, and analytics-oriented report widgets.
- **Documents & folders** — Optional paths to candidate document folders and lightweight file listing (local disk).
- **Settings** — Business display name, currency symbol, and a **backup directory** for scheduled-style exports.
- **Backup & restore** — JSON full backup (restore in-app), CSV export, and Excel-oriented backup where configured.

---

## Requirements

- **Windows 10 or 11**
- **Python 3.11+** (64-bit recommended) with `python` on `PATH`
- **Microsoft Edge** or **Google Chrome** (used in app mode for the UI)

---

## Quick start

1. Install Python from [python.org](https://www.python.org/downloads/) and enable **Add python.exe to PATH** during setup.
2. Double-click **`Start Globaris CRM.bat`** in this folder.  
   - First launch may take a moment while dependencies install (`pip install -r requirements.txt`).
3. The app listens on **`http://127.0.0.1:8765`**. A browser window should open automatically.
4. To stop: close the CRM window, or run **`Stop Globaris CRM.bat`** (stops whatever is listening on port **8765**).

**Manual start (developers):**

```bat
python -m pip install -r requirements.txt
python desktop_app.py
```

---

## Data storage

- **Database** (`immigration_crm.db`) is created in **this application folder** when you run the unfrozen (Python) app from here. It holds all cases, payments, deals, and settings.
- **Browser profile** for the embedded window is stored under your user profile (e.g. `%LOCALAPPDATA%\GlobarisCRM\browser-profile`) — not your CRM records.
- **Backups** — Configure the backup folder under **Settings**. JSON backups are suitable for full restores; CSV is for spreadsheets. Use **Import backup JSON** only with files created by this app’s backup/export flow.

---

## Configuration notes

- **Default backup path** in the UI is a placeholder (`C:\Globaris\Backups`). Set it to a real folder you control.
- **Primary operator** label in the finance logic is a neutral default (“Primary operator”). Adjust per deployment as needed in code if you require a different fixed label.
- **Port 8765** is defined in `desktop_app.py`. Change it there if you have a conflict (and update any bookmarked URLs).

---

## Project layout (this package)

| Path | Purpose |
|------|---------|
| `desktop_app.py` | Starts Uvicorn and opens the UI |
| `app/main.py` | FastAPI routes, business logic |
| `app/database.py` | SQLite path, schema, migrations |
| `app/backup_io.py` | Backup / restore / ID compaction helpers |
| `app/schemas.py` | Request/response models |
| `static/` | Frontend (`index.html`, `app.js`, `style.css`, icons) |
| `requirements.txt` | Python dependencies |
| `VERIFICATION_CHECKLIST.txt` | Suggested QA before releases |

---

## Security & privacy

- Designed for **local / trusted-network** use. There is no built-in multi-user authentication in this package.
- Do not expose port **8765** to the public internet without adding proper security (reverse proxy, TLS, auth).
- Treat exported JSON/CSV as **sensitive**; they contain applicant and financial information.

---

## Troubleshooting

- **“Pip install failed”** — Confirm `python --version` works in a new Command Prompt.
- **Port already in use** — Run `Stop Globaris CRM.bat` or exit another instance using port 8765.
- **Import JSON fails** — Use a **JSON** backup from this app, not a CSV/Excel file. UTF-8 (including BOM) is supported.
- **Case IDs change after deletes** — The app may renumber active case IDs to stay compact (`#1…#n`); this is intentional after deletes or maintenance.

---

## Verification

If you maintain a development tree with the full source, you can run automated smoke checks (health, clients, delete, import/export) via a small script if present in that tree. For shipped folders, use **`VERIFICATION_CHECKLIST.txt`**.


