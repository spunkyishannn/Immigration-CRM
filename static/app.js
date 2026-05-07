const STAGES = ["Documentation Done", "Permit Under Process", "Permit Approved", "Waiting for Visa Decision"];
const PAGE_TITLES = { dashboard: "Dashboard", cases: "Visa Cases", finance: "Finance", documents: "Documents", reports: "Reports", settings: "Settings" };
const PAGE_SUBS = {
  dashboard: "Operations + collections snapshot",
  cases: "Manage and track all your visa cases",
  finance: "Client payments, Finance Breakdown (Me, Partners, Recruiter)",
  documents: "Candidate folders",
  reports: "Finance and pipeline analytics",
  settings: "App configuration and folders",
};
const state = { clients: [], allClients: [], settings: { biz_name: "Immigration CRM", currency: "₹", backup_dir: "C:\\ImmigrationCRM\\Backups" }, page: "dashboard", caseSearch: "", folderClientId: null, fbClientId: null, fbBreakdown: null };
const INR = "₹";
const $ = (id) => document.getElementById(id);
const stageClass = (stage) => ({ "Documentation Done": "stage-doc", "Permit Under Process": "stage-process", "Permit Approved": "stage-approved", "Waiting for Visa Decision": "stage-wait" }[stage] || "stage-doc");
const stagePillClass = (stage) => ({ "Documentation Done": "doc", "Permit Under Process": "process", "Permit Approved": "approve", "Waiting for Visa Decision": "wait" }[stage] || "doc");
const inr = (num) => `${INR}${Number(num || 0).toLocaleString("en-IN")}`;
const chartRupeeLabel = (n) => {
  const v = Math.round(Number(n) || 0);
  return v === 0 ? "0" : `${INR}${v.toLocaleString("en-IN")}`;
};
const fmtDate = (v) => (v ? new Date(v.replace(" ", "T")).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const fmtDateTimeCompact = (v) => (v ? v.replace("T", " ") : "N/A");
const avatarColor = (name) => ["#c9a227", "#0e2348", "#0369a1", "#0d7a52", "#b45309", "#1d4ed8"][(name || "A").charCodeAt(0) % 6];
const RECEIPT_TYPE_OPTIONS = [
  { value: "advance", label: "Advance" },
  { value: "after_work_permit", label: "After Work Permit Issued" },
  { value: "after_visa", label: "After Visa" },
  { value: "uncertain", label: "Uncertain" },
];
const RECRUITER_PAID_VIA_OPTIONS = ["Polish Bank Card", "BLIK", "Indian transfer", "Indian Card"];
const CLIENT_PAYMENT_MODE_OPTIONS = ["Cash", "UPI", "Bank Transfer"];
const RECEIPT_IN_OPTIONS = ["Cash", "UPI", "Bank Transfer", "Bank account", "BLIK", "Polish Bank Card", "Indian transfer", "Indian Card"];
const RECEIPT_FROM_CLIENT = "Client";
const toast = (msg, type = "success") => { const t = $("toast"); t.textContent = `${type === "success" ? "✅" : "❌"} ${msg}`; t.className = `toast ${type}`; setTimeout(() => t.classList.add("show"), 10); setTimeout(() => t.classList.remove("show"), 2800); };
const closeModal = (id) => $(id).classList.remove("open");
const openModal = (id) => $(id).classList.add("open");

function apiErrorMessage(data, fallback) {
  if (data == null) return fallback;
  const d = data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (typeof x === "object" && x != null ? x.msg || JSON.stringify(x) : String(x))).join("; ") || fallback;
  if (typeof d === "object" && d !== null) return data.error || JSON.stringify(d);
  return data.error || fallback;
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = (res.headers.get("content-type") || "").includes("application/json") ? await res.json() : null;
  if (!res.ok) throw new Error(apiErrorMessage(data, "Request failed"));
  return data;
}

async function loadAll() {
  const [clientsRes, settings] = await Promise.all([api("/api/clients?page=1&limit=200"), api("/api/settings")]);
  state.clients = clientsRes.items || [];
  state.allClients = [...state.clients];
  state.settings = settings || state.settings;
}

function goTo(page, el) {
  state.page = page;
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  $(`page-${page}`).classList.add("active");
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.remove("active"));
  if (el) el.classList.add("active");
  $("page-title").textContent = PAGE_TITLES[page];
  $("page-sub").textContent = PAGE_SUBS[page];
  renderPage(page);
}

function renderPage(page) {
  Promise.resolve().then(async () => {
    if (page === "dashboard") await renderDashboard();
    if (page === "cases") await renderCasesTable();
    if (page === "finance") await renderFinance();
    if (page === "documents") renderDocuments();
    if (page === "reports") await renderReports();
    if (page === "settings") loadSettingsUI();
  }).catch((e) => toast(e.message, "error"));
}

function bookBarAndLegend(book) {
  const tf = Number(book?.total_fee || 0);
  const col = Number(book?.total_collected ?? book?.total_paid ?? 0);
  const out = Number(book?.outstanding ?? 0);
  if (tf <= 0) {
    return `<p class="task-meta">No total fees on clients yet.</p>
      <div class="dash-book-legend"><span>Collected: <strong>${inr(col)}</strong></span><span>Outstanding: <strong>${inr(out)}</strong></span></div>`;
  }
  const w = 300;
  const wCol = Math.min(w, Math.round((col / tf) * w));
  const wOut = Math.min(w - wCol, Math.round((out / tf) * w));
  const rest = Math.max(0, w - wCol - wOut);
  return `<div class="dash-book-bar" style="max-width:${w}px" aria-hidden="true"><span class="dash-book-seg-col" style="width:${wCol}px"></span><span class="dash-book-seg-out" style="width:${wOut}px"></span>${rest > 0 ? `<span style="flex:${rest};background:var(--cream-100)"></span>` : ""}</div>
    <div class="dash-book-legend"><span style="color:#0d7a52">Collected ${inr(col)}</span><span style="color:#b45309">Outstanding ${inr(out)}</span><span>Agreed ${inr(tf)}</span></div>`;
}

function collectionsAreaSvg(series, w = 340, h = 188) {
  const uid = `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const padL = 46;
  const padR = 10;
  const padT = 30;
  const padB = 32;
  const amounts = series.map((p) => Number(p.amount || 0));
  const maxY = Math.max(1, ...amounts);
  const xStep = amounts.length > 1 ? (w - padL - padR) / (amounts.length - 1) : 0;
  const toX = (i) => padL + i * xStep;
  const toY = (v) => (h - padB) - ((h - padT - padB) * (v / maxY));
  const pts = amounts.map((y, i) => `${toX(i)},${toY(y)}`).join(" ");
  const baseY = h - padB;
  const fillPath = `M ${toX(0)},${baseY} L ${amounts.map((y, i) => `${toX(i)},${toY(y)}`).join(" L ")} L ${toX(amounts.length - 1)},${baseY} Z`;
  const grid = [0.25, 0.5, 0.75]
    .map((g) => {
      const gy = padT + (h - padT - padB) * (1 - g);
      return `<line x1="${padL}" y1="${gy}" x2="${w - padR}" y2="${gy}" stroke="rgba(14,35,72,.08)" stroke-width="1"/>`;
    })
    .join("");
  const maxLab = maxY >= 1e5 ? `${Math.round(maxY / 1000)}k` : `${Math.round(maxY)}`;
  const yTicks = `<text x="${padL - 6}" y="${baseY + 3}" fill="#6e7184" font-size="9" text-anchor="end">0</text><text x="${padL - 6}" y="${toY(maxY) + 3}" fill="#6e7184" font-size="9" text-anchor="end">${maxLab}</text>`;
  const valLabs = amounts
    .map((y, i) => {
      const lx = toX(i);
      const ly = Math.max(12, toY(y) - 10);
      return `<text x="${lx}" y="${ly}" fill="#0e2348" font-size="9" font-weight="700" text-anchor="middle">${chartRupeeLabel(y)}</text>`;
    })
    .join("");
  const dots = amounts.map((y, i) => `<circle cx="${toX(i)}" cy="${toY(y)}" r="5" fill="#c9a227" filter="url(#${uid}-glow)" stroke="#fff" stroke-width="1.5"/>`).join("");
  const xLabs = series.map((p, i) => `<text x="${toX(i)}" y="${h - 8}" fill="#6e7184" font-size="9" text-anchor="middle">${escapeHtml(p.label || "")}</text>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="210" class="dash-svg-tech">
    <defs>
      <linearGradient id="${uid}-fill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="rgba(29,78,216,0.35)"/><stop offset="100%" stop-color="rgba(29,78,216,0.02)"/>
      </linearGradient>
      <linearGradient id="${uid}-line" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="#1d4ed8"/><stop offset="100%" stop-color="#06b6d4"/>
      </linearGradient>
      <filter id="${uid}-glow" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur stdDeviation="1.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    ${grid}
    ${yTicks}
    <path d="${fillPath}" fill="url(#${uid}-fill)" stroke="none"/>
    <polyline fill="none" stroke="url(#${uid}-line)" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" filter="url(#${uid}-glow)" points="${pts}"/>
    ${valLabs}${dots}${xLabs}</svg>`;
}

function monthlyNewCasesSvg(series, w = 340, h = 196) {
  const uid = `m${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const padL = 40;
  const padR = 12;
  const padT = 28;
  const padB = 30;
  const counts = series.map((p) => Number(p.count || 0));
  const maxY = Math.max(1, ...counts);
  const barW = counts.length ? ((w - padL - padR) / counts.length) * 0.62 : 0;
  const gap = counts.length ? (w - padL - padR) / counts.length : 0;
  const bars = counts
    .map((c, i) => {
      const x = padL + i * gap + (gap - barW) / 2;
      const bh = ((h - padT - padB) * c) / maxY;
      const y = h - padB - bh;
      const r = 4;
      const rect = `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(bh, c > 0 ? 0.5 : 1)}" rx="${r}" fill="url(#${uid}-bar)" opacity="0.92"/>`;
      const tx = x + barW / 2;
      const ty = c > 0 ? Math.max(padT + 10, y - 8) : h - padB - 14;
      const lab = `<text x="${tx}" y="${ty}" fill="${c > 0 ? "#0e2348" : "#9ca3af"}" font-size="10" font-weight="700" text-anchor="middle">${c}</text>`;
      return `<g>${rect}${lab}</g>`;
    })
    .join("");
  const xLabs = series
    .map((p, i) => {
      const cx = padL + i * gap + gap / 2;
      return `<text x="${cx}" y="${h - 8}" fill="#6e7184" font-size="9" text-anchor="middle">${escapeHtml(p.label || "")}</text>`;
    })
    .join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="210" class="dash-svg-tech">
    <defs>
      <linearGradient id="${uid}-bar" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#7c3aed"/><stop offset="100%" stop-color="#1e3a5f"/>
      </linearGradient>
    </defs>
    ${bars}${xLabs}
  </svg>`;
}

function recentCasesAndGeoHtml(op) {
  const countries = op.country_mix || [];
  const recent = op.recent_cases || [];
  const maxC = Math.max(1, ...countries.map((x) => Number(x.count || 0)));
  const geo =
    countries.length > 0
      ? `<div class="dash-geo-bars">${countries
          .map((x) => {
            const pct = (Number(x.count) / maxC) * 100;
            return `<div class="dash-geo-row"><span class="dash-geo-name">${escapeHtml(x.country || "")}</span><div class="dash-geo-track"><div class="dash-geo-fill" style="width:${pct}%"></div></div><span class="dash-geo-n">${x.count}</span></div>`;
          })
          .join("")}</div>`
      : `<p class="task-meta">No country data yet.</p>`;
  const rc =
    recent.length > 0
      ? `<div class="dash-recent-list">${recent
          .map(
            (c) =>
              `<div class="dash-recent-row" onclick="openClientDetail(${c.id})"><span class="dash-recent-av" style="background:${avatarColor(c.full_name)}">${(c.full_name || "?")[0]}</span><div class="dash-recent-meta"><div class="dash-recent-name">${escapeHtml(c.full_name || "")}</div><div class="dash-recent-sub"><span class="stage-pill ${stageClass(c.stage)}">${escapeHtml(c.stage || "")}</span> · ${escapeHtml(c.country || "—")}</div></div><span class="dash-recent-date">${fmtDate(c.updated_at)}</span></div>`,
          )
          .join("")}</div>`
      : `<p class="task-meta">No recent updates.</p>`;
  return `<div class="dash-split-2">${geo}<div class="dash-recent-block"><div class="dash-mini-head">Recently updated</div>${rc}</div></div>`;
}

function stageDonutSvg(pipeline, colors) {
  const totalStage = pipeline.reduce((s, p) => s + Number(p.count || 0), 0) || 1;
  const cx = 72;
  const cy = 72;
  const radius = 48;
  const c = 2 * Math.PI * radius;
  let offset = 0;
  const segs = pipeline.slice(0, 6).map((s, i) => {
    const pct = Number(s.count || 0) / totalStage;
    const dash = `${pct * c} ${c}`;
    const part = `<circle cx="${cx}" cy="${cy}" r="${radius}" fill="none" stroke="${colors[i % colors.length]}" stroke-width="12" stroke-dasharray="${dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})" opacity="0.95"/>`;
    offset += pct * c;
    return part;
  });
  return `<div class="donut-wrap donut-tech"><div><svg viewBox="0 0 144 144" width="100%" height="190">
    <defs><filter id="donutSh"><feGaussianBlur stdDeviation="0.5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
    <circle cx="${cx}" cy="${cy}" r="${radius}" fill="none" stroke="rgba(14,35,72,.12)" stroke-width="14"/>
    ${segs.join("")}
    <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="donut-center-tech">${totalStage}</text>
    <text x="${cx}" y="${cy + 14}" text-anchor="middle" class="donut-sub-tech">cases</text>
  </svg></div><div class="stage-name-list">${pipeline
    .slice(0, 6)
    .map(
      (s, i) =>
        `<div class="stage-name-item"><span class="dash-legend-dot" style="background:${colors[i % colors.length]}"></span><span class="stage-name-text">${escapeHtml(s.stage || "")}</span><strong class="stage-name-count">${s.count}</strong></div>`,
    )
    .join("")}</div></div>`;
}

function collectionsLineSvg(series, w = 320, h = 170) {
  const padL = 40;
  const padR = 8;
  const padT = 12;
  const padB = 28;
  const amounts = series.map((p) => Number(p.amount || 0));
  const maxY = Math.max(1, ...amounts);
  const xStep = amounts.length > 1 ? (w - padL - padR) / (amounts.length - 1) : 0;
  const toX = (i) => padL + i * xStep;
  const toY = (v) => (h - padB) - ((h - padT - padB) * (v / maxY));
  const poly = amounts.map((y, i) => `${toX(i)},${toY(y)}`).join(" ");
  const dots = amounts.map((y, i) => `<circle cx="${toX(i)}" cy="${toY(y)}" r="4" fill="#c9a227" stroke="#0e2348" stroke-width="1.5"/>`).join("");
  const xLabs = series.map((p, i) => `<text x="${toX(i)}" y="${h - 6}" fill="#6e7184" font-size="9" text-anchor="middle">${escapeHtml(p.label || "")}</text>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="180"><polyline fill="none" stroke="#0e2348" stroke-width="2.5" points="${poly}"/>${dots}${xLabs}</svg>`;
}

function outstandingTableHtml(rows) {
  if (!rows || !rows.length) return `<p class="task-meta">No outstanding balances — well done.</p>`;
  return `<table class="dash-out-table"><tbody>${rows
    .map(
      (r) =>
        `<tr><td>${escapeHtml(r.client_name || "")}</td><td class="num">${inr(r.balance)}</td></tr>`,
    )
    .join("")}</tbody></table>`;
}

function paymentModesHtml(modes) {
  if (!modes || !modes.length) return `<p class="task-meta">No Client→Partner payments recorded.</p>`;
  const maxAmt = Math.max(1e-9, ...modes.map((m) => Number(m.amount || 0)));
  return modes
    .map((m) => {
      const pct = (Number(m.amount || 0) / maxAmt) * 100;
      return `<div class="dash-mode-row"><span style="min-width:88px">${escapeHtml(m.payment_type || "")}</span><div class="dash-mode-bar"><div class="dash-mode-fill" style="width:${pct}%"></div></div><span style="min-width:72px;text-align:right">${inr(m.amount)}</span></div>`;
    })
    .join("");
}

function outstandingHorizontalBarsHtml(rows) {
  if (!rows || !rows.length) return `<p class="task-meta">No outstanding balances — well done.</p>`;
  const maxBal = Math.max(1e-9, ...rows.map((r) => Number(r.balance || 0)));
  return rows
    .map((r) => {
      const pct = (Number(r.balance || 0) / maxBal) * 100;
      return `<div class="dash-mode-row report-out-row"><span class="report-out-name">${escapeHtml(r.client_name || "")}</span><div class="dash-mode-bar"><div class="dash-mode-fill report-out-fill" style="width:${pct}%"></div></div><span class="report-out-amt">${inr(r.balance)}</span></div>`;
    })
    .join("");
}

function personalEconomicsHtml(p) {
  const taken = Number(p.personal_taken || 0);
  const come = Number(p.still_to_come ?? p.personal_to_come ?? 0);
  if (taken <= 0 && come <= 0) {
    return `<p class="task-meta">No Finance Breakdown economics to show yet (configure deals in Finance).</p>`;
  }
  const w = 300;
  const tot = taken + come;
  const wT = tot > 0 ? Math.min(w, Math.round((taken / tot) * w)) : 0;
  const wC = Math.min(w - wT, tot > 0 ? Math.round((come / tot) * w) : 0);
  const rest = Math.max(0, w - wT - wC);
  return `<div class="dash-book-bar personal-econ-bar" style="max-width:${w}px" aria-hidden="true"><span class="dash-book-seg-col" style="width:${wT}px"></span><span class="dash-book-seg-out" style="width:${wC}px"></span>${rest > 0 ? `<span style="flex:${rest};background:var(--cream-100)"></span>` : ""}</div>
    <div class="dash-book-legend"><span style="color:#0d7a52">Taken ${inr(taken)}</span><span style="color:#b45309">Still to come ${inr(come)}</span></div>
    <p class="field-hint" style="margin-top:10px">From profit split and settlement fields across all Finance Breakdowns.</p>`;
}

async function renderDashboard() {
  const data = await api("/api/dashboard");
  const fin = data.finance || {};
  const op = data.operational || {};
  const st = data.stats || {};
  $("stat-grid").innerHTML = `
    <div class="stat-card blue" onclick="quickStageFilter('')"><div class="kpi-main"><div class="kpi-left"><div class="stat-icon">👥</div><div class="stat-label">Case load</div></div><div class="kpi-right"><div class="stat-value">${st.total_cases}</div><div class="stat-sub">Active file</div></div></div><div class="kpi-spark kpi-spark-tech">${sparkLineSvg("#1d4ed8")}</div></div>
    <div class="stat-card orange" onclick="quickStageFilter('Permit Under Process')"><div class="kpi-main"><div class="kpi-left"><div class="stat-icon">⚙️</div><div class="stat-label">In process</div></div><div class="kpi-right"><div class="stat-value">${st.processing_cases}</div><div class="stat-sub">Permit / embassy work</div></div></div><div class="kpi-spark kpi-spark-tech">${sparkLineSvg("#b45309")}</div></div>
    <div class="stat-card green" onclick="quickStageFilter('Permit Approved')"><div class="kpi-main"><div class="kpi-left"><div class="stat-icon">✅</div><div class="stat-label">Approved</div></div><div class="kpi-right"><div class="stat-value">${st.approved_cases}</div><div class="stat-sub">Wins in pipeline</div></div></div><div class="kpi-spark kpi-spark-tech">${sparkLineSvg("#0d7a52")}</div></div>
    <div class="stat-card purple" onclick="goTo('finance', document.querySelector('[data-page=\"finance\"]'))"><div class="kpi-main"><div class="kpi-left"><div class="stat-icon">📥</div><div class="stat-label">Collected</div></div><div class="kpi-right"><div class="stat-value" style="font-size:1.05rem">${inr(fin.total_paid)}</div><div class="stat-sub">Open Finance for detail</div></div></div><div class="kpi-spark kpi-spark-tech">${sparkLineSvg("#c9a227")}</div></div>`;
  const colors = ["#6366f1", "#0e2348", "#0891b2", "#7c3aed", "#c9a227", "#0d7a52"];
  const pipeline = data.pipeline || [];
  $("pipeline-chart").innerHTML = pipeline.length ? stageDonutSvg(pipeline, colors) : `<p class="task-meta">No cases yet.</p>`;
  const collSeries = fin.collections_trend || [];
  $("dash-collections-chart").innerHTML =
    collSeries.length > 0 ? collectionsAreaSvg(collSeries) : `<p class="task-meta">No dated collections yet.</p>`;
  const opened = op.cases_opened_trend || [];
  $("dash-opened-chart").innerHTML =
    opened.length > 0 ? monthlyNewCasesSvg(opened) : `<p class="task-meta">No case creation dates yet.</p>`;
  $("dash-outstanding-list").innerHTML = `${outstandingTableHtml(fin.top_outstanding || [])}<p class="field-hint" style="margin-top:10px">Tap a case on the right to open details.</p>`;
  $("dash-recent-geo").innerHTML = recentCasesAndGeoHtml(op);
}

function sparkLineSvg(color) {
  return `<svg viewBox="0 0 120 28" width="100%" height="28" class="spark-tech"><polyline fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" points="2,22 18,18 32,20 48,12 62,14 78,8 94,14 118,6"/></svg>`;
}

function caseFilters() {
  wireCountryPreset();
  const s = $("filter-stage").value;
  const c = $("filter-country").value;
  const countries = [...new Set(state.allClients.map((x) => x.country).filter(Boolean))];
  $("filter-stage").innerHTML = `<option value="">All Stages</option>${STAGES.map((x) => `<option>${x}</option>`).join("")}`;
  $("filter-country").innerHTML = `<option value="">All Countries</option>${countries.map((x) => `<option>${escapeHtml(x)}</option>`).join("")}`;
  $("c-stage").innerHTML = STAGES.map((x) => `<option>${x}</option>`).join("");
  $("filter-stage").value = s;
  $("filter-country").value = c;
}

async function renderCasesTable() {
  caseFilters();
  const params = new URLSearchParams({ page: "1", limit: "200" });
  if ($("filter-stage").value) params.set("stage", $("filter-stage").value);
  if ($("filter-country").value) params.set("country", $("filter-country").value);
  if (state.caseSearch) params.set("search", state.caseSearch);
  const rows = (await api(`/api/clients?${params.toString()}`)).items || [];
  $("cases-table-body").innerHTML = rows.length ? rows.map((x) => `<tr><td>#${x.id}</td><td>${escapeHtml(x.full_name)}</td><td>${escapeHtml(x.phone || "—")}</td><td>${escapeHtml(x.country || "—")}</td><td>${escapeHtml(x.visa_type || "—")}</td><td><span class="stage-pill ${stageClass(x.stage)}">${escapeHtml(x.stage)}</span></td><td>${inr(x.total_fee)}</td><td>${escapeHtml(x.process_started_on || "—")}</td><td><div style="display:flex;gap:6px"><button type="button" class="btn btn-outline btn-sm" onclick="openClientDetail(${x.id})">View</button><button type="button" class="btn btn-outline btn-sm" onclick="openCaseModal(${x.id})">Edit</button><button type="button" class="btn btn-danger btn-sm" onclick="deleteCase(${x.id})">Delete</button></div></td></tr>`).join("") : `<tr><td colspan="9"><div class="empty-state">No cases found.</div></td></tr>`;
}

async function openCaseModal(id) {
  wireCountryPreset();
  $("case-id").value = id || "";
  const c = state.clients.find((x) => x.id === id);
  $("c-name").value = c?.full_name || "";
  $("c-phone").value = c?.phone || "";
  $("c-email").value = c?.email || "";
  applyCountryToForm(c?.country || "");
  $("c-visatype").value = c?.visa_type || "Full Time - Work Permit";
  $("c-stage").value = c?.stage || "Documentation Done";
  $("c-nationality").value = c?.nationality || "Indian";
  $("c-passport").value = c?.passport || "";
  $("c-process-started-on").value = c?.process_started_on || "";
  $("c-total-fee").value = c?.total_fee || "";
  $("c-recruiting-partners").value = "";
  if (id) {
    try {
      const deal = await api(`/api/deals/${id}`);
      $("c-recruiting-partners").value = deal.recruiting_partners || "";
    } catch (e) {
      /* ignore */
    }
  }
  openModal("case-modal");
}

async function saveCase() {
  const id = $("case-id").value;
  const payload = {
    full_name: $("c-name").value.trim(),
    phone: $("c-phone").value.trim(),
    email: $("c-email").value.trim(),
    country: countryValueFromForm(),
    visa_type: $("c-visatype").value.trim() || "Full Time - Work Permit",
    stage: $("c-stage").value,
    nationality: $("c-nationality").value.trim() || "Indian",
    passport: $("c-passport").value.trim(),
    process_started_on: $("c-process-started-on").value || null,
    total_fee: Number($("c-total-fee").value || 0),
    notes: "",
    recruiting_partners: $("c-recruiting-partners").value.trim(),
  };
  await api(id ? `/api/clients/${id}` : "/api/clients", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
  closeModal("case-modal"); await loadAll(); renderPage(state.page); toast("Case saved");
}
async function deleteCase(id) { if (!confirm("Delete this case?")) return; await api(`/api/clients/${id}`, { method: "DELETE" }); await loadAll(); renderPage(state.page); toast("Case deleted"); }

function populateCaseSelect(id, empty = false) { $(id).innerHTML = `${empty ? '<option value="">-- None --</option>' : ""}${state.clients.map((c) => `<option value="${c.id}">${escapeHtml(c.full_name)} (#${c.id})</option>`).join("")}`; }
function toDateTimeLocal(v) {
  if (!v) return "";
  const normalized = v.includes("T") ? v : v.replace(" ", "T");
  return normalized.slice(0, 16);
}

function countryValueFromForm() {
  const preset = $("c-country-preset")?.value || "Poland";
  if (preset === "Other") {
    const o = ($("c-country-other")?.value || "").trim();
    return o ? `Other: ${o}` : "Other";
  }
  return preset;
}

function applyCountryToForm(stored) {
  const presetEl = $("c-country-preset");
  const otherEl = $("c-country-other");
  if (!presetEl || !otherEl) return;
  const v = stored || "";
  if (v === "Poland" || v === "Maldives") {
    presetEl.value = v;
    otherEl.value = "";
    otherEl.style.display = "none";
    return;
  }
  if (v.startsWith("Other:")) {
    presetEl.value = "Other";
    otherEl.value = v.slice(6).trim();
    otherEl.style.display = "block";
    return;
  }
  if (v) {
    presetEl.value = "Other";
    otherEl.value = v;
    otherEl.style.display = "block";
    return;
  }
  presetEl.value = "Poland";
  otherEl.value = "";
  otherEl.style.display = "none";
}

function wireCountryPreset() {
  const presetEl = $("c-country-preset");
  const otherEl = $("c-country-other");
  if (!presetEl || !otherEl || presetEl.dataset.wired) return;
  presetEl.dataset.wired = "1";
  const sync = () => {
    otherEl.style.display = presetEl.value === "Other" ? "block" : "none";
    if (presetEl.value !== "Other") otherEl.value = "";
  };
  presetEl.addEventListener("change", sync);
  sync();
}

function receiptTypeSelectHtml(selected) {
  return `<select class="fb-cell-input fb-in-type">${RECEIPT_TYPE_OPTIONS.map((o) => `<option value="${o.value}" ${o.value === selected ? "selected" : ""}>${o.label}</option>`).join("")}</select>`;
}

function paidViaSelectHtml(selected) {
  return `<select class="fb-cell-input fb-r-pay-via">${RECRUITER_PAID_VIA_OPTIONS.map((o) => `<option value="${o}" ${o === selected ? "selected" : ""}>${o}</option>`).join("")}</select>`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function receiptFromSelectHtml(partnerNames, value) {
  const v = (value && String(value).trim()) || RECEIPT_FROM_CLIENT;
  const clientSel = v === RECEIPT_FROM_CLIENT ? " selected" : "";
  const clientOpt = `<option value="${encodeURIComponent(RECEIPT_FROM_CLIENT)}"${clientSel}>Client</option>`;
  const opts = partnerNames
    .map((p) => {
      const enc = encodeURIComponent(p);
      const sel = p === v ? " selected" : "";
      return `<option value="${enc}"${sel}>${escapeHtml(p)}</option>`;
    })
    .join("");
  return `<select class="fb-cell-input fb-in-from">${clientOpt}${opts}</select>`;
}

function partnerPaymentModeSelectHtml(selected) {
  let s = (selected && String(selected).trim()) || "UPI";
  const canon = CLIENT_PAYMENT_MODE_OPTIONS.find((o) => o.toLowerCase() === s.toLowerCase());
  if (canon) s = canon;
  const list = CLIENT_PAYMENT_MODE_OPTIONS.includes(s) ? [...CLIENT_PAYMENT_MODE_OPTIONS] : [s, ...CLIENT_PAYMENT_MODE_OPTIONS];
  return `<select class="fb-cell-input fb-cp-type">${list.map((o) => `<option value="${escapeHtml(o)}" ${o === s ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}</select>`;
}

function collectedBySelectHtml(partners, value) {
  const v = (value && String(value).trim()) || partners[0] || "";
  return `<select class="fb-cell-input fb-cp-by">${partners.map((p) => `<option value="${escapeHtml(p)}" ${p === v ? "selected" : ""}>${escapeHtml(p)}</option>`).join("")}</select>`;
}

function buildClientPartnerPaymentRow(partners, row = {}) {
  const pid = row.id != null ? row.id : "";
  const by = row.collected_by || partners[0] || "";
  const pt = row.payment_type || "UPI";
  return `<tr data-payment-id="${pid}">
    <td><input type="number" class="fb-cell-input fb-cp-amt" step="0.01" min="0" value="${row.amount_paid != null ? row.amount_paid : ""}"></td>
    <td>${partnerPaymentModeSelectHtml(pt)}</td>
    <td>${collectedBySelectHtml(partners, by)}</td>
    <td><input type="datetime-local" class="fb-cell-input fb-cp-at" value="${toDateTimeLocal(row.paid_at || "")}"></td>
    <td><button type="button" class="btn btn-outline btn-sm fb-btn-remove">Remove</button></td>
  </tr>`;
}

function receivedInSelectHtml(stored) {
  const raw = (stored && String(stored).trim()) || "";
  let opts = [...RECEIPT_IN_OPTIONS];
  if (raw && !opts.includes(raw)) opts = [raw, ...opts];
  const sel = raw && opts.includes(raw) ? raw : "UPI";
  return `<select class="fb-cell-input fb-in-channel">${opts.map((o) => `<option value="${escapeHtml(o)}" ${o === sel ? "selected" : ""}>${escapeHtml(o)}</option>`).join("")}</select>`;
}

function buildReceiptRow(partnerNames, row = {}) {
  const fromDecoded = row.received_from || "";
  const rt = row.receipt_type || "advance";
  return `<tr>
    <td><input type="number" class="fb-cell-input fb-in-amt" step="0.01" min="0" value="${row.amount != null ? row.amount : ""}"></td>
    <td>${receivedInSelectHtml(row.received_in || "")}</td>
    <td>${receiptFromSelectHtml(partnerNames, fromDecoded)}</td>
    <td>${receiptTypeSelectHtml(rt)}</td>
    <td><input type="number" class="fb-cell-input fb-in-settle" step="0.01" min="0" value="${row.share_settlement != null ? row.share_settlement : ""}"></td>
    <td><input type="datetime-local" class="fb-cell-input fb-in-at" value="${toDateTimeLocal(row.received_at || "")}"></td>
    <td><button type="button" class="btn btn-outline btn-sm fb-btn-remove">Remove</button></td>
  </tr>`;
}

function buildRecruiterRow(row = {}) {
  const via = row.paid_via || RECRUITER_PAID_VIA_OPTIONS[0];
  return `<tr>
    <td><input type="number" class="fb-cell-input fb-r-amt" step="0.01" min="0" value="${row.amount != null ? row.amount : ""}"></td>
    <td>${paidViaSelectHtml(via)}</td>
    <td><input type="datetime-local" class="fb-cell-input fb-r-at" value="${toDateTimeLocal(row.paid_at || "")}"></td>
    <td><button type="button" class="btn btn-outline btn-sm fb-btn-remove">Remove</button></td>
  </tr>`;
}

function wireFbTableRemove(tbody) {
  tbody.querySelectorAll(".fb-btn-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.closest("tr")?.remove();
    });
  });
}

function renderFbPctGrid() {
  const data = state.fbBreakdown;
  if (!data) return;
  const handlers = data.handler_names || [];
  const pmap = Object.fromEntries((data.handler_pcts || []).map((x) => [x.name, x.pct]));
  $("fb-pct-grid").innerHTML = handlers
    .map(
      (h) =>
        `<div class="form-group"><label>${escapeHtml(h)} (%)</label><input type="number" class="fb-pct-input" step="0.01" min="0" max="100" value="${pmap[h] != null ? pmap[h] : ""}"></div>`,
    )
    .join("");
}

function renderFbHandlerBlocks() {
  const data = state.fbBreakdown;
  const el = $("fb-handler-blocks");
  if (!el || !data) return;
  const handlers = data.handler_names || [];
  el.innerHTML = handlers.map((h) => `<div class="fb-handler-chip">${escapeHtml(h)}</div>`).join("");
}

function toggleFbSplitModeUi() {
  const custom = $("fb-split-custom").checked;
  $("fb-pct-grid").style.display = custom ? "grid" : "none";
  $("fb-equal-hint").style.display = custom ? "none" : "block";
}

function renderFbComputed(computed) {
  const c = computed || {};
  $("fb-computed-box").innerHTML = `
    <div class="fb-computed-block"><strong>My personal — money I have taken</strong> (this Client)<div class="fb-computed-amt">${inr(c.personal_taken)}</div><p class="fb-computed-note">Σ amounts in <strong>Section 3</strong> minus Σ payments in <strong>Section 4</strong> (cash in your pocket for this case).</p></div>
    <div class="fb-computed-block"><strong>Money still to come</strong> (this Client)<div class="fb-computed-amt">${inr(c.money_still_to_come)}</div><p class="fb-computed-note">Your share of <strong>Profit to split</strong> minus what you already counted via <strong>Count toward my share settlement</strong> in Section 3.</p></div>
    <div class="fb-computed-block"><strong>Your share of Profit to split</strong><div class="fb-computed-amt">${inr(c.my_share_target)}</div><p class="fb-computed-note">Your cut of the <strong>Profit to split (₹)</strong> from Section 1 — equal split across handlers, or your custom % if you chose that.</p></div>
    <div class="fb-computed-block"><strong>Σ Count toward my share settlement</strong><div class="fb-computed-amt">${inr(c.share_settlement_credited)}</div><p class="fb-computed-note">Total of the <strong>Count toward my share settlement (₹)</strong> column in Section 3 — money that reduces &quot;still to come&quot; when a receipt is only partly your profit share (e.g. pass-through).</p></div>`;
}

async function loadFinanceBreakdown() {
  const clientId = Number($("fb-client-select").value || 0);
  state.fbClientId = clientId || null;
  if (!clientId) {
    $("fb-panel").hidden = true;
    return;
  }
  const data = await api(`/api/finance/breakdown/${clientId}`);
  state.fbBreakdown = data;
  $("fb-panel").hidden = false;
  const tcf = $("fb-total-client-fee");
  if (tcf) tcf.value = data.total_client_fee != null ? data.total_client_fee : "";
  $("fb-money-collected").textContent = inr(data.money_collected);
  $("fb-profit-to-split").value = data.deal?.profit_to_split != null ? data.deal.profit_to_split : "";
  const rft = $("fb-recruiter-fees-total");
  if (rft) rft.value = data.recruiter_fees_total != null ? data.recruiter_fees_total : "";
  const mode = data.deal?.profit_split_mode || "equal";
  $("fb-split-equal").checked = mode === "equal";
  $("fb-split-custom").checked = mode === "custom_pct";
  toggleFbSplitModeUi();
  renderFbHandlerBlocks();
  renderFbPctGrid();
  const partners = data.partner_names || [];
  const cpp = data.client_partner_payments || [];
  const cpbody = $("fb-client-payments-body");
  const cphint = $("fb-client-payments-hint");
  const addCp = $("fb-add-client-payment-btn");
  if (!partners.length) {
    if (cphint) cphint.textContent = "Add Recruiting partners on the Client record to record Client → Partner payments here (Section 2 syncs with the payment table).";
    if (cpbody) cpbody.innerHTML = `<tr><td colspan="5" class="fb-muted-cell">No Indian partners on this case — Section 2 is only for partner collections.</td></tr>`;
    if (addCp) addCp.disabled = true;
  } else {
    if (cphint) cphint.textContent = "";
    if (addCp) addCp.disabled = false;
    if (cpbody) {
      cpbody.innerHTML = cpp.length ? cpp.map((r) => buildClientPartnerPaymentRow(partners, r)).join("") : buildClientPartnerPaymentRow(partners, {});
      wireFbTableRemove(cpbody);
    }
  }
  const rbody = $("fb-receipts-body");
  const receipts = data.partner_receipts || [];
  rbody.innerHTML = receipts.length ? receipts.map((r) => buildReceiptRow(partners, r)).join("") : buildReceiptRow(partners, {});
  wireFbTableRemove(rbody);
  const recbody = $("fb-recruiter-body");
  const rpay = data.recruiter_payments || [];
  recbody.innerHTML = rpay.length ? rpay.map((r) => buildRecruiterRow(r)).join("") : buildRecruiterRow({});
  wireFbTableRemove(recbody);
  renderFbComputed(data.computed);
}

function collectBreakdownPayload() {
  const handlerNames = state.fbBreakdown?.handler_names || [];
  const custom = $("fb-split-custom").checked;
  const inputs = [...document.querySelectorAll("#fb-pct-grid .fb-pct-input")];
  const handler_pcts = handlerNames.map((h, i) => ({ name: h, pct: Number(inputs[i]?.value || 0) }));
  const receiptRows = [...document.querySelectorAll("#fb-receipts-body tr")].map((tr) => {
    const fromSel = tr.querySelector(".fb-in-from");
    let received_from = "";
    if (fromSel && fromSel.value) {
      try {
        received_from = decodeURIComponent(fromSel.value);
      } catch {
        received_from = fromSel.value;
      }
    }
    const chSel = tr.querySelector(".fb-in-channel");
    const received_in = chSel?.value?.trim() || "";
    return {
      amount: Number(tr.querySelector(".fb-in-amt")?.value || 0),
      received_in,
      received_from,
      receipt_type: tr.querySelector(".fb-in-type")?.value || "advance",
      share_settlement: Number(tr.querySelector(".fb-in-settle")?.value || 0),
      received_at: tr.querySelector(".fb-in-at")?.value || null,
    };
  });
  const recruiterRows = [...document.querySelectorAll("#fb-recruiter-body tr")].map((tr) => ({
    amount: Number(tr.querySelector(".fb-r-amt")?.value || 0),
    paid_via: tr.querySelector(".fb-r-pay-via")?.value || RECRUITER_PAID_VIA_OPTIONS[0],
    paid_at: tr.querySelector(".fb-r-at")?.value || null,
  }));
  const clientPayRows = [...document.querySelectorAll("#fb-client-payments-body tr")].filter((tr) => tr.querySelector(".fb-cp-amt")).map((tr) => {
    const pid = tr.dataset.paymentId;
    return {
      id: pid ? Number(pid) : undefined,
      amount_paid: Number(tr.querySelector(".fb-cp-amt")?.value || 0),
      payment_type: tr.querySelector(".fb-cp-type")?.value || "UPI",
      collected_by: tr.querySelector(".fb-cp-by")?.value?.trim() || "",
      paid_at: tr.querySelector(".fb-cp-at")?.value || null,
      description: "",
    };
  });
  const total_client_fee = Number($("fb-total-client-fee")?.value || 0);
  const recruiter_fees_total = Number($("fb-recruiter-fees-total")?.value || 0);
  return {
    total_client_fee,
    profit_to_split: Number($("fb-profit-to-split").value || 0),
    profit_split_mode: custom ? "custom_pct" : "equal",
    recruiter_fees_total,
    handler_pcts: custom ? handler_pcts : [],
    client_partner_payments: clientPayRows.filter((r) => Number(r.amount_paid) > 0),
    partner_receipts: receiptRows.filter((r) => Number(r.amount) > 0),
    recruiter_payments: recruiterRows.filter((r) => Number(r.amount) > 0),
  };
}

async function saveFinanceBreakdown() {
  const clientId = Number($("fb-client-select").value || 0);
  if (!clientId) return toast("Choose a Client and load deal", "error");
  if ($("fb-split-custom").checked) {
    const h = state.fbBreakdown?.handler_names || [];
    const inputs = [...document.querySelectorAll("#fb-pct-grid .fb-pct-input")];
    const total = h.reduce((s, _, i) => s + Number(inputs[i]?.value || 0), 0);
    if (h.length && Math.abs(total - 100) > 0.05) return toast("Custom percentages must sum to 100%", "error");
  }
  const payload = collectBreakdownPayload();
  const data = await api(`/api/finance/breakdown/${clientId}`, { method: "PUT", body: JSON.stringify(payload) });
  state.fbBreakdown = data;
  renderFbComputed(data.computed);
  await loadAll();
  await renderFinance();
  toast("Finance Breakdown saved");
}

async function renderFinance() {
  const [itemsRes, personalRes] = await Promise.all([api("/api/finance/summary"), api("/api/finance/personal-summary")]);
  const items = itemsRes.items || [];
  const pt = Number(personalRes.personal_taken || 0);
  const pc = Number(personalRes.personal_to_come || 0);
  const total = items.reduce((s, x) => s + Number(x.total_fee || 0), 0);
  const paid = items.reduce((s, x) => s + Number(x.total_paid || 0), 0);
  $("finance-summary").innerHTML = `<div class="fin-card fin-card-personal"><div class="label">My personal — money I have taken</div><div class="amount" style="color:var(--green)">${inr(pt)}</div><div class="fin-hint">All <strong>Clients</strong>: Σ (Money transferred to me − Money to <strong>Recruiter</strong>) per Finance Breakdown.</div></div><div class="fin-card fin-card-personal"><div class="label">Money still to come</div><div class="amount" style="color:var(--navy)">${inr(pc)}</div><div class="fin-hint">From <strong>Profit to split</strong> and <strong>Final Profit split criteria</strong>, minus <strong>Count toward my share settlement</strong>.</div></div><div class="fin-card"><div class="label">Total fees agreed (all Clients)</div><div class="amount" style="color:var(--blue)">${inr(total)}</div></div><div class="fin-card"><div class="label">Money collected from Clients</div><div class="amount" style="color:var(--green)">${inr(paid)}</div></div><div class="fin-card"><div class="label">Outstanding from Clients</div><div class="amount" style="color:var(--orange)">${inr(total - paid)}</div></div>`;
  $("finance-table-body").innerHTML = items.map((x) => `<tr class="fin-main-row"><td><span class="fin-client-name">#${x.client_id} - ${escapeHtml(x.client_name)}</span></td><td>${inr(x.total_fee)}</td><td>${inr(x.total_paid)}</td><td>${inr(x.balance)}</td><td>${escapeHtml(x.status)}</td><td><button type="button" class="btn btn-outline btn-sm" onclick="togglePayments(${x.client_id})">View</button> <button type="button" class="btn btn-primary btn-sm" onclick="openPaymentModal('',${x.client_id})">Add</button></td></tr><tr id="tx-${x.client_id}" class="fin-sub-row" style="display:none"><td colspan="6"><div id="tx-body-${x.client_id}">Loading...</div></td></tr>`).join("");
  $("fb-client-select").innerHTML = `<option value="">Select Client</option>${state.clients.map((c) => `<option value="${c.id}">#${c.id} - ${escapeHtml(c.full_name)}</option>`).join("")}`;
  if (!state.fbClientId && state.clients.length) state.fbClientId = state.clients[0].id;
  if (state.fbClientId) {
    $("fb-client-select").value = String(state.fbClientId);
    await loadFinanceBreakdown().catch((e) => toast(e.message, "error"));
  } else {
    $("fb-panel").hidden = true;
  }
}
async function openPaymentModal(id, clientId) {
  populateCaseSelect("pay-case");
  let p = null;
  if (id) p = ((await api(`/api/payments?client_id=${clientId}`)).items || []).find((x) => x.id === id) || null;
  $("pay-id").value = id || "";
  $("pay-case").value = clientId || "";
  $("pay-desc").value = p?.description || "";
  $("pay-amount").value = p?.amount_paid || "";
  $("pay-type").value = p?.payment_type || "UPI";
  $("pay-collected-by").value = p?.collected_by || "";
  const today = new Date().toISOString().slice(0, 10);
  $("pay-datetime").value = p ? toDateTimeLocal(p.paid_at) : `${today}T00:00`;
  openModal("payment-modal");
}
async function savePayment() {
  const id = $("pay-id").value;
  const payload = { client_id: Number($("pay-case").value), description: $("pay-desc").value, amount_paid: Number($("pay-amount").value || 0), payment_type: $("pay-type").value, collected_by: $("pay-collected-by").value, paid_at: $("pay-datetime").value || null };
  await api(id ? `/api/payments/${id}` : "/api/payments", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
  closeModal("payment-modal"); await loadAll(); renderFinance(); renderDashboard(); toast("Payment saved");
}
async function deletePayment(id) { if (!confirm("Delete payment?")) return; await api(`/api/payments/${id}`, { method: "DELETE" }); await loadAll(); renderFinance(); renderDashboard(); toast("Payment deleted"); }

async function togglePayments(clientId) {
  const row = $(`tx-${clientId}`);
  const body = $(`tx-body-${clientId}`);
  row.style.display = row.style.display === "none" ? "table-row" : "none";
  if (row.style.display === "table-row") {
    const [txRes, bd] = await Promise.all([
      api(`/api/payments?client_id=${clientId}`),
      api(`/api/finance/breakdown/${clientId}`).catch(() => null),
    ]);
    const tx = txRes.items || [];
    const lastCollector = tx.length ? (tx[0].collected_by || "").trim() : "";
    const payBlock = tx.length
      ? `<div class="tx-list"><div class="tx-row tx-head"><span>Amount</span><span>Type</span><span>Date/Time</span><span>Collected By</span><span>Actions</span></div>${tx.map((p) => `<div class="tx-row"><span><strong>${inr(p.amount_paid)}</strong></span><span>${p.payment_type || "N/A"}</span><span>${fmtDateTimeCompact(p.paid_at)}</span><span>${p.collected_by || "N/A"}</span><span class="tx-actions"><button class="btn btn-outline btn-sm" onclick="openPaymentModal(${p.id},${clientId})">Edit</button><button class="btn btn-danger btn-sm" onclick="deletePayment(${p.id})">Delete</button></span></div>`).join("")}</div>`
      : "<p class=\"task-meta\">No Client → Partner payments in this list yet.</p>";
    const directRows = (bd?.partner_receipts || []).filter((r) => (r.received_from || "").trim() === "Client" && Number(r.amount || 0) > 0);
    const directBlock =
      directRows.length > 0
        ? `<div class="partner-receipt-block"><div class="partner-receipt-title">Client paid you directly (Finance Breakdown §3)</div><div class="tx-list"><div class="tx-row tx-head"><span>Amount</span><span>Channel</span><span>Type</span><span>Date/Time</span></div>${directRows
            .map(
              (r) =>
                `<div class="tx-row"><span><strong>${inr(r.amount)}</strong></span><span>${escapeHtml(r.received_in || "—")}</span><span>${escapeHtml(r.receipt_type || "—")}</span><span>${fmtDateTimeCompact(r.received_at)}</span></div>`,
            )
            .join("")}</div><p class="task-meta" style="margin-top:8px">Edit or add these in <strong>Finance Breakdown</strong> → Section 3 (<strong>Save Finance Breakdown</strong>).</p></div>`
        : "";
    const hint = `<p class="task-meta" style="margin-top:10px">Money <strong>Partners</strong> send <strong>to Me</strong> and payments <strong>to Recruiter</strong> are in <strong>Finance Breakdown</strong> below.</p>`;
    body.innerHTML = `${payBlock}${directBlock}${lastCollector ? `<p class="task-meta" style="margin-top:10px">Tip: last Client→Partner collected by <strong>${escapeHtml(lastCollector)}</strong></p>` : ""}${hint}`;
  }
}

function renderDocuments() {
  const sorted = [...state.clients].sort((a, b) => Number(a.id) - Number(b.id));
  $("documents-cards").innerHTML = sorted
    .map(
      (c) =>
        `<div class="doc-card" onclick="openFolderModal(${c.id},'${c.full_name.replace(/'/g, "\\'")}')"><div class="doc-card-top"><span class="doc-id-badge">#${c.id}</span><div class="doc-photo" style="background:${avatarColor(c.full_name)}">${c.full_name[0]}</div></div><div class="doc-card-body"><strong>${c.full_name}</strong><div class="task-meta"><span class="stage-pill ${stageClass(c.stage)}">${c.stage}</span></div></div></div>`,
    )
    .join("");
}
async function openFolderModal(clientId, name) {
  state.folderClientId = clientId;
  const info = await api(`/api/clients/${clientId}/folder-files`);
  $("folder-modal-title").textContent = `Folder - ${name}`;
  $("folder-files-list").innerHTML = info.folder_path ? `<div class="task-meta" style="margin-bottom:8px">Path: ${info.folder_path}</div>${(info.files || []).map((f) => `<div class="task-meta" style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)"><span>${f.name}</span><button class="btn btn-outline btn-sm" onclick="downloadFolderFile('${encodeURIComponent(f.name)}')">Download</button></div>`).join("") || "<div class='task-meta'>No files.</div>"}` : "<div class='task-meta'>Path not set. Set in Settings.</div>";
  openModal("folder-modal");
}
function downloadFolderFile(encoded) { if (state.folderClientId) window.location.href = `/api/clients/${state.folderClientId}/folder-files/download?name=${encoded}`; }
async function uploadFolderFile(file) { if (!state.folderClientId) return toast("Open client folder first", "error"); const fd = new FormData(); fd.append("file", file); await fetch(`/api/clients/${state.folderClientId}/folder-files`, { method: "POST", body: fd }); await openFolderModal(state.folderClientId, `Client #${state.folderClientId}`); }
async function openFolderPath() { if (!state.folderClientId) return; await api(`/api/clients/${state.folderClientId}/open-folder`, { method: "POST" }); }

async function renderReports() {
  const a = await api("/api/reports/analytics");
  const book = a.book || {};
  $("report-chart-book").innerHTML = bookBarAndLegend(book);
  $("report-chart-outstanding").innerHTML = outstandingHorizontalBarsHtml(a.top_outstanding || []);
  const series = a.collections_by_month || [];
    $("report-chart-collections").innerHTML =
    series.length > 0 ? collectionsAreaSvg(series, 360, 200) : `<p class="task-meta">No dated collections yet.</p>`;
  $("report-chart-personal").innerHTML = personalEconomicsHtml(a.personal || {});
}

function loadSettingsUI() {
  $("setting-biz").value = state.settings.biz_name || "Immigration CRM";
  $("setting-backup-dir").value = state.settings.backup_dir || "C:\\ImmigrationCRM\\Backups";
  $("setting-folder-client").innerHTML = `<option value="">Select Client</option>${[...state.clients].sort((a, b) => Number(a.id) - Number(b.id)).map((c) => `<option value="${c.id}">#${c.id} - ${escapeHtml(c.full_name)}</option>`).join("")}`;
}
async function saveSettings() {
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      biz_name: $("setting-biz").value.trim(),
      backup_dir: $("setting-backup-dir").value.trim() || "C:\\ImmigrationCRM\\Backups",
    }),
  });
  await loadAll();
  loadSettingsUI();
  toast("Settings saved");
}
async function saveFolderPath() { const id = Number($("setting-folder-client").value || 0); const path = $("setting-folder-path").value.trim(); if (!id || !path) return toast("Select client and set path", "error"); await api(`/api/clients/${id}/folder-path`, { method: "PUT", body: JSON.stringify({ folder_path: path }) }); toast("Folder path saved"); }
async function importData(event) { const file = event.target.files[0]; if (!file) return; const text = await file.text(); await api("/api/import/json", { method: "POST", body: text }); await loadAll(); renderPage(state.page); toast("Data imported"); }
async function createBackupNow() {
  const info = await api("/api/backup/create", { method: "POST", body: JSON.stringify({}) });
  if (info.created) {
    const xl = info.excel_path || info.csv_path;
    const js = info.json_path || "";
    toast(`Backup saved — Excel + JSON. ${xl || ""}${js ? " | " + js : ""}`);
  }
  else if (info.reason === "backup_error") toast(`Backup failed: ${info.error}`, "error");
  else toast("No data changes since last backup");
}

async function globalRefresh() {
  const r = await api("/api/clients/reindex-ids", { method: "POST", body: "{}" });
  await loadAll();
  await renderPage(state.page);
  toast(r.changed ? "Case IDs renumbered to #1…#n and data reloaded." : "IDs already sequential — data reloaded.");
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((n) => n.addEventListener("click", () => goTo(n.dataset.page, n)));
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => closeModal(b.dataset.close)));
  document.querySelectorAll(".modal-overlay").forEach((m) => m.addEventListener("click", (e) => { if (e.target === m) m.classList.remove("open"); }));
  $("global-refresh-btn").addEventListener("click", () => globalRefresh().catch((e) => toast(e.message, "error")));
  $("add-case-btn").addEventListener("click", () => openCaseModal().catch((e) => toast(e.message, "error")));
  $("save-case-btn").addEventListener("click", () => saveCase().catch((e) => toast(e.message, "error")));
  $("add-payment-btn").addEventListener("click", () => openPaymentModal("", ""));
  $("save-payment-btn").addEventListener("click", savePayment);
  $("filter-stage").addEventListener("change", renderCasesTable);
  $("filter-country").addEventListener("change", renderCasesTable);
  $("folder-upload-file").addEventListener("change", async (e) => { if (e.target.files[0]) await uploadFolderFile(e.target.files[0]); e.target.value = ""; });
  $("open-folder-btn").addEventListener("click", async () => { try { await openFolderPath(); } catch (e) { toast(e.message, "error"); } });
  $("global-search").addEventListener("input", async (e) => { state.caseSearch = e.target.value.trim(); goTo("cases", document.querySelector('[data-page="cases"]')); await renderCasesTable(); });
  document.querySelector('[data-page="cases"]').addEventListener("click", async () => {
    if (!state.caseSearch) return;
    state.caseSearch = "";
    $("global-search").value = "";
    await renderCasesTable();
  });
  $("save-settings-btn").addEventListener("click", saveSettings);
  $("create-backup-btn").addEventListener("click", createBackupNow);
  $("save-folder-path-btn").addEventListener("click", saveFolderPath);
  $("export-json-btn").addEventListener("click", () => { window.location.href = "/api/export/csv"; });
  $("import-json-file").addEventListener("change", importData);
  $("fb-load-btn").addEventListener("click", () => loadFinanceBreakdown().catch((e) => toast(e.message, "error")));
  $("fb-save-btn").addEventListener("click", () => saveFinanceBreakdown().catch((e) => toast(e.message, "error")));
  $("fb-client-select").addEventListener("change", () => {
    state.fbClientId = Number($("fb-client-select").value || 0) || null;
    loadFinanceBreakdown().catch((e) => toast(e.message, "error"));
  });
  $("fb-split-equal").addEventListener("change", toggleFbSplitModeUi);
  $("fb-split-custom").addEventListener("change", toggleFbSplitModeUi);
  $("fb-add-receipt-btn").addEventListener("click", () => {
    const partners = state.fbBreakdown?.partner_names || [];
    const tbody = $("fb-receipts-body");
    tbody.insertAdjacentHTML("beforeend", buildReceiptRow(partners, {}));
    wireFbTableRemove(tbody);
  });
  $("fb-add-recruiter-btn").addEventListener("click", () => {
    const tbody = $("fb-recruiter-body");
    tbody.insertAdjacentHTML("beforeend", buildRecruiterRow({}));
    wireFbTableRemove(tbody);
  });
  $("fb-add-client-payment-btn").addEventListener("click", () => {
    const partners = state.fbBreakdown?.partner_names || [];
    if (!partners.length) return toast("Add Recruiting partners on the case first", "error");
    const tbody = $("fb-client-payments-body");
    tbody.insertAdjacentHTML("beforeend", buildClientPartnerPaymentRow(partners, {}));
    wireFbTableRemove(tbody);
  });
}

async function openClientDetail(clientId) {
  const d = await api(`/api/clients/${clientId}/detail`);
  const fin = d.finance;
  $("client-detail-body").innerHTML = `<div class="card" style="margin-bottom:12px"><div class="card-title">Basic Info</div><div>${escapeHtml(d.client.full_name)} | ${escapeHtml(d.client.phone)} | ${escapeHtml(d.client.email || "N/A")}<br>${escapeHtml(d.client.country)} | ${escapeHtml(d.client.visa_type)} | <span class="stage-pill ${stageClass(d.client.stage)}">${escapeHtml(d.client.stage)}</span><br>Process Started On: ${escapeHtml(d.client.process_started_on || "—")}</div></div><div class="card"><div class="card-title">Financial</div><div>Total Fee: ${inr(fin.total_fee)} | Total Paid: ${inr(fin.total_paid)} | Balance: ${inr(fin.balance)} | Status: ${escapeHtml(fin.status)}</div></div><div style="margin-top:10px"><button type="button" class="btn btn-primary" onclick="openCaseModal(${clientId});closeModal('client-detail-modal')">Edit Client</button></div>`;
  openModal("client-detail-modal");
}
function quickStageFilter(stage) { goTo("cases", document.querySelector('[data-page="cases"]')); $("filter-stage").value = stage; renderCasesTable(); }

window.openCaseModal = openCaseModal;
window.deleteCase = deleteCase;
window.openPaymentModal = openPaymentModal;
window.deletePayment = deletePayment;
window.togglePayments = togglePayments;
window.openClientDetail = openClientDetail;
window.quickStageFilter = quickStageFilter;
window.downloadFolderFile = downloadFolderFile;
window.openFolderPath = openFolderPath;

(async function bootstrap() { try { wireCountryPreset(); bindEvents(); await loadAll(); renderPage("dashboard"); } catch (e) { toast(e.message, "error"); } })();
