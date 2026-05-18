/**
 * Credit Pull standalone UI.
 * Reads API key from URL query param `?key=...` (or localStorage as fallback)
 * and sends it as X-API-Key on every API call.
 */

// ── Auth bootstrap ────────────────────────────────────────────────────
const API_KEY = (() => {
  const fromUrl = new URLSearchParams(window.location.search).get("key");
  if (fromUrl) {
    // Persist so refreshes work without the URL param
    try { localStorage.setItem("credit_pull_api_key", fromUrl); } catch (e) {}
    return fromUrl;
  }
  try { return localStorage.getItem("credit_pull_api_key"); } catch (e) { return null; }
})();

if (!API_KEY) {
  document.getElementById("cpAuthError")?.classList.remove("hidden");
  document.getElementById("cpApp")?.classList.add("hidden");
  throw new Error("No API key");
}
document.getElementById("cpApp")?.classList.remove("hidden");

// fetch wrapper that adds X-API-Key + JSON Content-Type
async function apiFetch(url, options = {}) {
  const headers = { "X-API-Key": API_KEY, ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    try { localStorage.removeItem("credit_pull_api_key"); } catch (e) {}
    alert("Authentication failed. Please reopen this page from your application's link.");
  }
  return res;
}

// ── DOM helpers ───────────────────────────────────────────────────────
const $    = (id) => document.getElementById(id);
const show = (el) => el?.classList.remove("hidden");
const hide = (el) => el?.classList.add("hidden");

function escapeStr(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function statusClass(status) {
  if (!status) return "";
  const s = String(status).toUpperCase();
  if (s === "OK") return "ok";
  if (["30","60","90","120"].includes(s)) return "late";
  if (["CO","CHARGEOFF"].includes(s)) return "bad";
  return "";
}

function formatMonth(ym) {
  const m = /^(\d{4})-(\d{2})$/.exec(ym);
  if (!m) return ym;
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return months[parseInt(m[2], 10) - 1] + " " + m[1].slice(2);
}

function collectMonths(ph) {
  const set = new Set();
  ["transunion","experian","equifax"].forEach(b => {
    Object.keys(ph[b] || {}).forEach(m => set.add(m));
  });
  return [...set].sort().reverse();
}

// ── State ─────────────────────────────────────────────────────────────
let currentSubjectId = null;
let currentReportId = null;
let currentQuestionId = null;
let currentQuestionAttempt = 0;
let pollIntervalId = null;
let isRetryingCreds = false;

const VIEWS = ["cpSubjectList","cpNewSubjectForm","cpPullForm","cpStatusView","cpReportView"];

function showView(viewId) {
  VIEWS.forEach(id => hide($(id)));
  show($(viewId));
}

// ── Init ──────────────────────────────────────────────────────────────
$("cpBtnNewSubject")?.addEventListener("click", () => showView("cpNewSubjectForm"));
$("cpBtnCancelSubject")?.addEventListener("click", () => showView("cpSubjectList"));
$("cpBtnSaveSubject")?.addEventListener("click", saveSubject);
$("cpBtnCancelPull")?.addEventListener("click", () => showView("cpSubjectList"));
$("cpBtnStartPull")?.addEventListener("click", startPull);
$("cpBtnSubmitAnswer")?.addEventListener("click", submitAnswer);
$("cpBtnBackToSubjects")?.addEventListener("click", () => { stopPolling(); activate(); });

function activate() {
  stopPolling();
  isRetryingCreds = false;
  hide($("cpPullFormError"));
  showView("cpSubjectList");
  loadSubjects();
}

// ── Subjects ──────────────────────────────────────────────────────────
async function loadSubjects() {
  const c = $("cpSubjectsContainer");
  c.innerHTML = '<p class="muted">Loading...</p>';
  try {
    const data = await (await apiFetch("/api/creditpull/subjects")).json();
    if (!data.subjects?.length) {
      c.innerHTML = '<p class="muted">No subjects yet. Click "+ New Subject".</p>';
      return;
    }
    c.innerHTML = data.subjects.map(s => `
      <div class="cp-subject-row">
        <div>
          <strong>${escapeStr(s.full_name)}</strong>
          ${s.date_of_birth ? `<span class="muted"> · DOB ${escapeStr(s.date_of_birth)}</span>` : ""}
          ${s.email ? `<div class="small">${escapeStr(s.email)}</div>` : ""}
        </div>
        <button class="cp-btn-pull" data-id="${s.id}" data-name="${escapeStr(s.full_name)}">Pull Report</button>
      </div>
    `).join("");
    c.querySelectorAll(".cp-btn-pull").forEach(b =>
      b.addEventListener("click", () => openPullForm(b.dataset.id, b.dataset.name)));
  } catch (e) {
    c.innerHTML = `<p class="muted">Error loading subjects: ${escapeStr(e.message)}</p>`;
  }
}

async function saveSubject() {
  const payload = {
    full_name:     $("cpInputName").value.trim(),
    date_of_birth: $("cpInputDOB").value || null,
    ssn_last4:     $("cpInputSSN").value.trim() || null,
    email:         $("cpInputEmail").value.trim() || null,
    phone:         $("cpInputPhone").value.trim() || null,
  };
  if (!payload.full_name) { alert("Full name is required."); return; }
  try {
    const res = await apiFetch("/api/creditpull/subjects", { method: "POST", body: JSON.stringify(payload) });
    if (!res.ok) { const err = await res.json().catch(() => ({})); alert("Error: " + (err.error || res.statusText)); return; }
    ["cpInputName","cpInputDOB","cpInputSSN","cpInputEmail","cpInputPhone"].forEach(id => $(id).value = "");
    showView("cpSubjectList");
    loadSubjects();
  } catch (e) { alert("Network error: " + e.message); }
}

// ── Pull form ─────────────────────────────────────────────────────────
function openPullForm(id, name) {
  currentSubjectId = id; isRetryingCreds = false; hide($("cpPullFormError"));
  $("cpPullSubjectName").textContent = name;
  $("cpInputIIQUsername").value = ""; $("cpInputIIQPassword").value = "";
  showView("cpPullForm");
}

async function startPull() {
  if (isRetryingCreds) return submitRetryCredentials();
  const u = $("cpInputIIQUsername").value.trim();
  const p = $("cpInputIIQPassword").value;
  if (!u || !p) { alert("IIQ username and password are required."); return; }
  try {
    const res = await apiFetch("/api/creditpull/run", {
      method: "POST",
      body: JSON.stringify({ subject_id: currentSubjectId, iiq_username: u, iiq_password: p }),
    });
    $("cpInputIIQUsername").value = ""; $("cpInputIIQPassword").value = "";
    if (!res.ok && res.status !== 202) {
      const err = await res.json().catch(() => ({})); alert("Error: " + (err.error || res.statusText)); return;
    }
    const data = await res.json();
    currentReportId = data.report_id;
    $("cpStatusSubjectName").textContent = $("cpPullSubjectName").textContent;
    $("cpStatusLabel").textContent = "Starting pull...";
    hide($("cpSecurityQForm"));
    showView("cpStatusView");
    startPolling();
  } catch (e) { alert("Network error: " + e.message); }
}

async function submitRetryCredentials() {
  const u = $("cpInputIIQUsername").value.trim();
  const p = $("cpInputIIQPassword").value;
  if (!u || !p) { alert("IIQ username and password are required."); return; }
  if (!currentReportId) { alert("No active pull to retry."); return; }
  try {
    const res = await apiFetch("/api/creditpull/credentials/" + currentReportId, {
      method: "POST",
      body: JSON.stringify({ iiq_username: u, iiq_password: p }),
    });
    $("cpInputIIQUsername").value = ""; $("cpInputIIQPassword").value = "";
    if (!res.ok) { const err = await res.json().catch(() => ({})); alert("Error: " + (err.error || res.statusText)); return; }
    hide($("cpPullFormError"));
    $("cpStatusLabel").textContent = "Retrying with new credentials...";
    showView("cpStatusView");
  } catch (e) { alert("Network error: " + e.message); }
}

// ── Polling ───────────────────────────────────────────────────────────
function startPolling() { pollStatus(); pollIntervalId = setInterval(pollStatus, 2000); }
function stopPolling() {
  if (pollIntervalId) { clearInterval(pollIntervalId); pollIntervalId = null; }
  currentQuestionId = null; currentQuestionAttempt = 0;
}

async function pollStatus() {
  if (!currentReportId) return;
  try {
    const res = await apiFetch("/api/creditpull/status/" + currentReportId);
    if (!res.ok) return;
    const data = await res.json();

    if (data.status === "awaiting_credentials") {
      isRetryingCreds = true; hide($("cpSecurityQForm")); currentQuestionId = null;
      const e = $("cpPullFormError");
      e.textContent = data.error_message || "Invalid credentials. Please try again.";
      show(e);
      $("cpInputIIQUsername").value = ""; $("cpInputIIQPassword").value = "";
      showView("cpPullForm");
      return;
    }
    if (isRetryingCreds && data.status !== "awaiting_credentials") {
      isRetryingCreds = false; hide($("cpPullFormError"));
    }

    if (data.status === "awaiting_input" && data.question) {
      const att = data.question.attempt_count || 1;
      const isNewQ = data.question.id !== currentQuestionId;
      const isRetry = !isNewQ && att > currentQuestionAttempt;
      if (isNewQ || isRetry) showSecurityQuestion(data.question, isRetry ? data.error_message : null);
      $("cpStatusLabel").textContent = "Waiting for your answer...";
    } else {
      hide($("cpSecurityQForm")); currentQuestionId = null; currentQuestionAttempt = 0;
      $("cpStatusLabel").textContent = "Status: " + data.status;
    }

    if (data.status === "done") {
      stopPolling(); $("cpStatusLabel").textContent = "Loading report..."; loadReport(currentReportId);
    } else if (data.status === "failed") {
      stopPolling();
      $("cpStatusLabel").innerHTML =
        '<div class="cp-form-error" style="margin:0">' +
        escapeStr(data.error_message || "Pull failed (unknown error).") + "</div>";
    }
  } catch (e) { console.error("creditpull poll error:", e); }
}

// ── Security Q form ───────────────────────────────────────────────────
function showSecurityQuestion(q, errorMessage = null) {
  currentQuestionId = q.id; currentQuestionAttempt = q.attempt_count || 1;
  $("cpSecurityQText").textContent = q.text;
  const e = $("cpSecurityQError");
  if (errorMessage) { e.textContent = errorMessage; show(e); } else { hide(e); }
  if (q.type === "choice" && Array.isArray(q.options)) {
    hide($("cpSecurityQTextInput")); show($("cpSecurityQChoiceInput"));
    $("cpSecurityQChoiceInput").innerHTML = q.options.map(o =>
      `<label><input type="radio" name="cpSecurityQChoice" value="${escapeStr(o)}"> ${escapeStr(o)}</label>`).join("");
  } else {
    show($("cpSecurityQTextInput")); hide($("cpSecurityQChoiceInput"));
    $("cpSecurityQAnswerText").value = "";
  }
  show($("cpSecurityQForm"));
}

async function submitAnswer() {
  if (!currentQuestionId) return;
  let answer;
  if (!$("cpSecurityQTextInput").classList.contains("hidden")) {
    answer = $("cpSecurityQAnswerText").value.trim();
  } else {
    answer = (document.querySelector('input[name="cpSecurityQChoice"]:checked') || {}).value;
  }
  if (!answer) { alert("Please provide an answer."); return; }
  try {
    await apiFetch("/api/creditpull/answer/" + currentQuestionId, {
      method: "POST", body: JSON.stringify({ answer }),
    });
    hide($("cpSecurityQForm"));
    $("cpStatusLabel").textContent = "Continuing...";
  } catch (e) { alert("Network error: " + e.message); }
}

// ── Report rendering ──────────────────────────────────────────────────
async function loadReport(id) {
  try {
    const report = await (await apiFetch("/api/creditpull/report/" + id)).json();
    renderReport(report);
    showView("cpReportView");
  } catch (e) { alert("Network error loading report: " + e.message); }
}

function renderReport(report) {
  const h = report.header || {};
  $("cpReportHeader").innerHTML =
    '<div class="cp-report-header">' +
    '<span><strong>Reference #:</strong> ' + escapeStr(h.reference_number || "-") + "</span>" +
    '<span><strong>Report Date:</strong> ' + escapeStr(h.report_date || "-") + "</span>" +
    "</div>";
  renderSectionTable($("cpReportPersonalInfo"), "Personal Information", report.personal_information || []);
  renderCreditScore($("cpReportCreditScore"), report.credit_score || {});
  renderSectionTable($("cpReportSummary"), "Summary", report.summary || []);
  renderAccountHistory($("cpReportAccountHistory"), report.accounts || []);
}

function renderSectionTable(c, title, rows) {
  c.innerHTML = "<h4>" + escapeStr(title) + "</h4>" +
    '<table class="cp-table"><thead><tr><th></th>' +
    '<th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EX">Experian</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th></tr></thead><tbody>' +
    rows.map(r =>
      "<tr><td><strong>" + escapeStr(r.field) + "</strong></td>" +
      "<td>" + escapeStr(r.transunion ?? "-") + "</td>" +
      "<td>" + escapeStr(r.experian   ?? "-") + "</td>" +
      "<td>" + escapeStr(r.equifax    ?? "-") + "</td></tr>"
    ).join("") + "</tbody></table>";
}

function renderCreditScore(c, cs) {
  const sc = cs.scores || {}, fa = cs.risk_factors || {};
  c.innerHTML = "<h4>Credit Score</h4>" +
    '<table class="cp-table"><thead><tr><th></th>' +
    '<th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EX">Experian</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th></tr></thead><tbody>' +
    [["score","Credit Score"], ["lender_rank","Lender Rank"], ["score_scale","Score Scale"]]
      .map(([k, label]) =>
        "<tr><td><strong>" + label + "</strong></td>" +
        ["transunion","experian","equifax"].map(b =>
          "<td>" + escapeStr(sc[b]?.[k] ?? "-") + "</td>").join("") + "</tr>")
      .join("") +
    "</tbody></table>" +
    '<div class="cp-risk-factors"><strong>Risk Factors</strong>' +
    ["transunion","experian","equifax"].map(b =>
      "<div><em>" + b.charAt(0).toUpperCase()+b.slice(1) + ":</em>" +
      ((fa[b]||[]).length === 0
        ? '<span class="muted"> none</span>'
        : "<ul>" + (fa[b]||[]).map(f => "<li>" + escapeStr(f) + "</li>").join("") + "</ul>") +
      "</div>").join("") + "</div>";
}

function renderAccountHistory(c, accounts) {
  if (!accounts.length) { c.innerHTML = '<h4>Account History</h4><p class="muted">No accounts.</p>'; return; }
  c.innerHTML = "<h4>Account History (" + accounts.length + " account" + (accounts.length===1?"":"s") + ")</h4>" +
    accounts.map(renderAccountCard).join("");
}

function renderAccountCard(a) {
  const fields = a.fields || [];
  const ph = a.payment_history || {transunion:{}, experian:{}, equifax:{}};
  const months = collectMonths(ph);
  return '<div class="cp-account-card"><div class="cp-account-card-header">' + escapeStr(a.creditor_name||"-") + "</div>" +
    '<table class="cp-table"><thead><tr><th></th>' +
    '<th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EX">Experian</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th></tr></thead><tbody>' +
    fields.map(f => "<tr><td><strong>" + escapeStr(f.field) + "</strong></td>" +
      "<td>" + escapeStr(f.transunion ?? "-") + "</td>" +
      "<td>" + escapeStr(f.experian   ?? "-") + "</td>" +
      "<td>" + escapeStr(f.equifax    ?? "-") + "</td></tr>").join("") + "</tbody></table>" +
    (months.length ? ('<div style="padding:8px 14px;font-weight:600;background:#f1f5f9;">Two-Year Payment History</div>' +
      '<table class="cp-table cp-payment-grid"><thead><tr><th></th>' +
      months.map(m => "<th>" + escapeStr(formatMonth(m)) + "</th>").join("") + "</tr></thead><tbody>" +
      ["transunion","experian","equifax"].map(b =>
        "<tr><td><strong>" + b.charAt(0).toUpperCase()+b.slice(1) + "</strong></td>" +
        months.map(m => { const st = (ph[b]&&ph[b][m]) || ""; return '<td class="' + statusClass(st) + '">' + escapeStr(st||"-") + "</td>"; }).join("") + "</tr>"
      ).join("") + "</tbody></table>") : "") + "</div>";
}

// Boot
activate();
