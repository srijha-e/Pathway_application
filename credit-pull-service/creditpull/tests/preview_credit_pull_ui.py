"""
Generate a self-contained, clickable demo of the entire Credit Pull UI.

Mocks the backend so you can walk through every view (subject list,
new subject form, IIQ creds form, status panel, security Q prompt,
report display) exactly as the underwriter would — without running
Flask, Supabase, or Playwright.

Run with the ED HEAT fixture (default):
    ./venv/bin/python Statements/creditpull/tests/preview_credit_pull_ui.py

Run with the latest LIVE scraped data from your local Postgres:
    ./venv/bin/python Statements/creditpull/tests/preview_credit_pull_ui.py --from-db

Run with a specific report ID from the DB:
    ./venv/bin/python Statements/creditpull/tests/preview_credit_pull_ui.py --report-id <uuid>

Open the printed file path in your browser. No server required.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
PUB_ROOT = PKG_ROOT.parent / "pub"
FIXTURE  = PKG_ROOT / "fixtures" / "sample_parsed_report.json"
CSS      = PUB_ROOT / "css" / "creditpull.css"

OUT_DIR  = HERE / "_preview"
OUT_FILE = OUT_DIR / "credit_pull_demo.html"


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Credit Pull — interactive UI demo</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      background: #f8fafc; margin: 0; padding: 0; color: #1e293b;
    }}
    .demo-bar {{
      background: #fef3c7; border-bottom: 1px solid #fbbf24;
      padding: 10px 20px; display: flex; align-items: center; gap: 16px;
      position: sticky; top: 0; z-index: 100;
    }}
    .demo-bar strong {{ color: #92400e; }}
    .demo-bar select, .demo-bar button {{
      padding: 4px 10px; border: 1px solid #d4b463; border-radius: 4px;
      background: white; font-size: 13px; cursor: pointer;
    }}
    .demo-stage {{ padding: 24px; max-width: 1200px; margin: 0 auto; }}
    .card {{
      background: #fff; border-radius: 8px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
      padding: 24px;
    }}
    .row {{ display: flex; gap: 16px; }}
    .row > div {{ flex: 1; }}
    .row-tight {{ display: flex; justify-content: space-between; align-items: center; }}
    label {{ display: block; font-size: 13px; font-weight: 500; margin-bottom: 4px; color: #475569; }}
    input[type="text"], input[type="email"], input[type="tel"], input[type="password"], input[type="date"] {{
      width: 100%; box-sizing: border-box; padding: 8px 10px;
      border: 1px solid #cbd5e1; border-radius: 4px; font-size: 14px;
    }}
    button {{
      padding: 8px 16px; border: none; border-radius: 4px;
      background: #2563eb; color: white; font-weight: 500; cursor: pointer;
      font-size: 14px;
    }}
    button.secondary {{ background: transparent; color: #475569; border: 1px solid #cbd5e1; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .actions {{ display: flex; gap: 10px; margin-top: 12px; }}
    .muted {{ color: #64748b; font-size: 13px; }}
    .hidden {{ display: none !important; }}
    h2, h3, h4 {{ margin-top: 0; }}

    /* Inlined production creditpull.css */
{css}
  </style>
</head>
<body>

<div class="demo-bar">
  <strong>UI DEMO</strong>
  <span class="muted">Backend is mocked. All five views are clickable end-to-end.</span>
  <label style="margin: 0;">Scenario:
    <select id="demoScenario">
      <option value="happy">Happy path (security Q → answer right → report)</option>
      <option value="bad_creds">Bad credentials → re-enter → success</option>
      <option value="wrong_answer">Wrong security Q answer → retry banner → right</option>
      <option value="subscription_required">Subscription required → upgrade prompt</option>
    </select>
  </label>
  <button onclick="resetDemo()">⟲ Reset</button>
</div>

<div class="demo-stage">
  <div class="card">
    <h2 style="margin-top:0">Credit Pull</h2>

    <!-- View 1: Subject list -->
    <div id="cpSubjectList">
      <div class="row-tight" style="margin-bottom:12px;">
        <h3 style="margin:0">Subjects</h3>
        <button id="cpBtnNewSubject">+ New Subject</button>
      </div>
      <div id="cpSubjectsContainer"></div>
    </div>

    <!-- View 2: New subject form -->
    <div id="cpNewSubjectForm" class="hidden">
      <h3>New Subject</h3>
      <div class="row">
        <div><label>Full Name *</label><input id="cpInputName" type="text" placeholder="ED HEAT" /></div>
        <div><label>Date of Birth</label><input id="cpInputDOB" type="date" /></div>
      </div>
      <div class="row" style="margin-top:8px;">
        <div><label>SSN last 4</label><input id="cpInputSSN" type="text" maxlength="4" placeholder="1234" /></div>
        <div><label>Email</label><input id="cpInputEmail" type="email" placeholder="applicant@example.com" /></div>
      </div>
      <div class="row" style="margin-top:8px;">
        <div><label>Phone</label><input id="cpInputPhone" type="tel" placeholder="555-555-5555" /></div>
      </div>
      <div class="actions" style="margin-top:14px;">
        <button id="cpBtnSaveSubject">Save Subject</button>
        <button id="cpBtnCancelSubject" class="secondary">Cancel</button>
      </div>
    </div>

    <!-- View 3: Pull report form (IIQ credentials) -->
    <div id="cpPullForm" class="hidden">
      <h3>Pull Credit Report</h3>
      <p class="muted">Subject: <strong><span id="cpPullSubjectName"></span></strong></p>
      <p id="cpPullFormError" class="cp-form-error hidden"></p>
      <div class="row">
        <div><label>IIQ Username *</label><input id="cpInputIIQUsername" type="text" autocomplete="off" /></div>
        <div><label>IIQ Password *</label><input id="cpInputIIQPassword" type="password" autocomplete="new-password" /></div>
      </div>
      <p class="muted" style="margin-top:8px;">Credentials are used in-memory for the scrape and never stored.</p>
      <div class="actions">
        <button id="cpBtnStartPull">Start Pull</button>
        <button id="cpBtnCancelPull" class="secondary">Cancel</button>
      </div>
    </div>

    <!-- View 4: Status display + security Q form -->
    <div id="cpStatusView" class="hidden">
      <h3>Pulling Credit Report</h3>
      <p class="muted">Subject: <strong><span id="cpStatusSubjectName"></span></strong></p>

      <div class="cp-status-block">
        <span class="cp-status-label" id="cpStatusLabel">Initializing...</span>
      </div>

      <div id="cpSecurityQForm" class="cp-security-q hidden">
        <h4 id="cpSecurityQText"></h4>
        <p id="cpSecurityQError" class="cp-form-error hidden"></p>
        <div id="cpSecurityQTextInput" class="hidden">
          <input id="cpSecurityQAnswerText" type="text" />
        </div>
        <div id="cpSecurityQChoiceInput" class="hidden"></div>
        <div class="actions" style="margin-top:10px;">
          <button id="cpBtnSubmitAnswer">Submit Answer</button>
        </div>
      </div>
    </div>

    <!-- View 5: Report display -->
    <div id="cpReportView" class="hidden">
      <div class="row-tight" style="margin-bottom:8px;">
        <h3 style="margin:0">Credit Report</h3>
        <button id="cpBtnBackToSubjects" class="secondary">← Back to Subjects</button>
      </div>
      <div id="cpReportHeader"></div>
      <div id="cpReportPersonalInfo"   class="cp-section"></div>
      <div id="cpReportCreditScore"    class="cp-section"></div>
      <div id="cpReportSummary"        class="cp-section"></div>
      <div id="cpReportAccountHistory" class="cp-section"></div>
    </div>
  </div>
</div>

<script>
// ════════════════════════════════════════════════════════════════════
//  MOCKED BACKEND — scripted responses based on selected scenario
// ════════════════════════════════════════════════════════════════════

const FIXTURE_REPORT = {fixture};

// Demo state machine. The mock only advances state in response to USER
// actions (submitting creds or answering a Q), never on poll count alone.
// That mirrors how the real backend behaves: the scraper waits for user
// input via DB poll before transitioning.
let DEMO = {{
  scenario: 'happy',
  reportId: null,
  pollCount: 0,
  credsResubmitted: false,
  answerSubmitCount: 0,      // 0=not answered, 1=answered once, 2=answered twice
  lastActionAt: null,        // timestamp of the most recent user action
}};

function resetDemo() {{
  DEMO = {{ scenario: document.getElementById('demoScenario').value,
            reportId: null, pollCount: 0, credsResubmitted: false,
            answerSubmitCount: 0, lastActionAt: null }};
  // Reset any visible state in the UI
  ['cpInputName','cpInputDOB','cpInputSSN','cpInputEmail','cpInputPhone',
   'cpInputIIQUsername','cpInputIIQPassword','cpSecurityQAnswerText'].forEach(id => {{
    const el = document.getElementById(id); if (el) el.value = '';
  }});
  hide($("cpPullFormError"));
  hide($("cpSecurityQError"));
  activateCreditPull();
}}

document.getElementById('demoScenario').addEventListener('change', resetDemo);

// Mock subjects (the backend would normally serve these from the DB)
const MOCK_SUBJECTS = {subjects};

// Override fetch() with our mock router.
window.fetch = async function(url, opts) {{
  const method = (opts && opts.method) || 'GET';
  const body = opts && opts.body ? JSON.parse(opts.body) : null;

  // Tiny delay to simulate network
  await new Promise(r => setTimeout(r, 200));

  // GET /subjects
  if (url === '/api/creditpull/subjects' && method === 'GET') {{
    return jsonResp({{ subjects: MOCK_SUBJECTS }});
  }}

  // POST /subjects
  if (url === '/api/creditpull/subjects' && method === 'POST') {{
    const newSubj = {{ id: 's' + Date.now(), ...body, created_at: new Date().toISOString() }};
    MOCK_SUBJECTS.unshift(newSubj);
    return jsonResp(newSubj, 201);
  }}

  // POST /run
  if (url === '/api/creditpull/run' && method === 'POST') {{
    DEMO.reportId = 'demo-' + Date.now();
    DEMO.pollCount = 0;
    DEMO.credsResubmitted = false;
    DEMO.answerSubmitCount = 0;
    DEMO.lastActionAt = Date.now();
    return jsonResp({{ report_id: DEMO.reportId, status: 'pending' }}, 202);
  }}

  // POST /credentials/<id>
  if (url.startsWith('/api/creditpull/credentials/') && method === 'POST') {{
    DEMO.credsResubmitted = true;
    DEMO.lastActionAt = Date.now();
    return jsonResp({{ ok: true }});
  }}

  // POST /answer/<qid>
  if (url.startsWith('/api/creditpull/answer/') && method === 'POST') {{
    DEMO.answerSubmitCount++;
    DEMO.lastActionAt = Date.now();
    return jsonResp({{ ok: true }});
  }}

  // GET /status/<id>
  if (url.startsWith('/api/creditpull/status/') && method === 'GET') {{
    DEMO.pollCount++;
    return jsonResp(scenarioStatus());
  }}

  // GET /report/<id>
  if (url.startsWith('/api/creditpull/report/') && method === 'GET') {{
    return jsonResp(FIXTURE_REPORT);
  }}

  return new Response('not mocked: ' + url, {{ status: 404 }});
}};

function jsonResp(obj, status = 200) {{
  return new Response(JSON.stringify(obj), {{
    status, headers: {{ 'Content-Type': 'application/json' }}
  }});
}}

// Returns the /status response for the current scenario.
// Action-driven: only advances state when the USER has done something
// (submit creds / submit answer). Never advances on poll count alone.
function scenarioStatus() {{
  const s = DEMO.scenario;
  const now = Date.now();
  const Q = {{ id: 'q1', text: 'Last four digits of your SSN?', type: 'text', attempt_count: 1 }};
  const sinceAction = DEMO.lastActionAt ? (now - DEMO.lastActionAt) : Infinity;

  if (s === 'happy') {{
    if (DEMO.pollCount === 1) return {{ status: 'running' }};
    // Block on the security question until user answers
    if (DEMO.answerSubmitCount === 0) {{
      return {{ status: 'awaiting_input', question: Q }};
    }}
    // After answer: brief "running" pause, then done
    if (sinceAction < 2000) return {{ status: 'running' }};
    return {{ status: 'done' }};
  }}

  if (s === 'bad_creds') {{
    if (DEMO.pollCount === 1) return {{ status: 'running' }};
    if (!DEMO.credsResubmitted) {{
      return {{
        status: 'awaiting_credentials',
        error_message: 'Invalid IIQ credentials. Please re-enter and try again.',
      }};
    }}
    // After creds resubmit: brief running pause
    if (sinceAction < 2000) return {{ status: 'running' }};
    // Then security Q until user answers
    if (DEMO.answerSubmitCount === 0) {{
      return {{ status: 'awaiting_input', question: Q }};
    }}
    if (sinceAction < 2000) return {{ status: 'running' }};
    return {{ status: 'done' }};
  }}

  if (s === 'wrong_answer') {{
    if (DEMO.pollCount === 1) return {{ status: 'running' }};
    // First time presenting Q
    if (DEMO.answerSubmitCount === 0) {{
      return {{ status: 'awaiting_input', question: Q }};
    }}
    // After 1st answer: pretend it was wrong, show retry banner with attempt 2
    if (DEMO.answerSubmitCount === 1) {{
      return {{
        status: 'awaiting_input',
        error_message: 'Wrong answer. Please try again (attempt 2 of 3).',
        question: {{ ...Q, attempt_count: 2 }},
      }};
    }}
    // After 2nd answer: brief running, then done
    if (sinceAction < 2000) return {{ status: 'running' }};
    return {{ status: 'done' }};
  }}

  if (s === 'subscription_required') {{
    // Pretend login + nav succeeded; running for ~3 polls, then fail with
    // the upgrade-required message.
    if (DEMO.pollCount <= 3) return {{ status: 'running' }};
    return {{
      status: 'failed',
      error_message: 'User requires upgrade in subscription. Please try after upgrade.',
    }};
  }}

  return {{ status: 'running' }};
}}

// ════════════════════════════════════════════════════════════════════
//  PRODUCTION FRONTEND — adapted from creditpull.js
//  (no behavioral changes; just inlined here so the demo is single-file)
// ════════════════════════════════════════════════════════════════════

const $    = (id) => document.getElementById(id);
const show = (el) => el?.classList.remove("hidden");
const hide = (el) => el?.classList.add("hidden");

let currentSubjectId = null;
let currentReportId = null;
let currentQuestionId = null;
let currentQuestionAttempt = 0;
let pollIntervalId = null;
let isRetryingCreds = false;

const VIEWS = ['cpSubjectList', 'cpNewSubjectForm', 'cpPullForm', 'cpStatusView', 'cpReportView'];

function showView(viewId) {{
  VIEWS.forEach(id => hide($(id)));
  show($(viewId));
}}

function escapeStr(s) {{
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({{ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }})[c]);
}}

function statusClass(status) {{
  if (!status) return '';
  const s = String(status).toUpperCase();
  if (s === 'OK') return 'ok';
  if (['30','60','90','120'].includes(s)) return 'late';
  if (['CO','CHARGEOFF'].includes(s)) return 'bad';
  return '';
}}

function formatMonth(ym) {{
  const m = /^(\\d{{4}})-(\\d{{2}})$/.exec(ym);
  if (!m) return ym;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return months[parseInt(m[2],10)-1] + ' ' + m[1].slice(2);
}}

function collectMonths(ph) {{
  const set = new Set();
  ['transunion','experian','equifax'].forEach(b => {{
    Object.keys(ph[b] || {{}}).forEach(m => set.add(m));
  }});
  return [...set].sort().reverse();
}}

// init handlers
$("cpBtnNewSubject")?.addEventListener("click", () => showView("cpNewSubjectForm"));
$("cpBtnCancelSubject")?.addEventListener("click", () => showView("cpSubjectList"));
$("cpBtnSaveSubject")?.addEventListener("click", saveSubject);
$("cpBtnCancelPull")?.addEventListener("click", () => showView("cpSubjectList"));
$("cpBtnStartPull")?.addEventListener("click", startPull);
$("cpBtnSubmitAnswer")?.addEventListener("click", submitAnswer);
$("cpBtnBackToSubjects")?.addEventListener("click", () => {{ stopPolling(); activateCreditPull(); }});

function activateCreditPull() {{
  stopPolling();
  isRetryingCreds = false;
  hide($("cpPullFormError"));
  showView("cpSubjectList");
  loadSubjects();
}}

async function loadSubjects() {{
  const c = $("cpSubjectsContainer");
  c.innerHTML = '<p class="muted">Loading...</p>';
  const data = await (await fetch('/api/creditpull/subjects')).json();
  if (!data.subjects.length) {{
    c.innerHTML = '<p class="muted">No subjects yet. Click "+ New Subject".</p>'; return;
  }}
  c.innerHTML = data.subjects.map(s => `
    <div class="cp-subject-row">
      <div>
        <strong>${{escapeStr(s.full_name)}}</strong>
        ${{s.date_of_birth ? '<span class="muted"> · DOB ' + escapeStr(s.date_of_birth) + '</span>' : ''}}
        ${{s.email ? '<div class="small">' + escapeStr(s.email) + '</div>' : ''}}
      </div>
      <button class="cp-btn-pull" data-id="${{s.id}}" data-name="${{escapeStr(s.full_name)}}">Pull Report</button>
    </div>
  `).join('');
  c.querySelectorAll('.cp-btn-pull').forEach(b => b.addEventListener('click', () => openPullForm(b.dataset.id, b.dataset.name)));
}}

async function saveSubject() {{
  const payload = {{ full_name: $("cpInputName").value.trim(), date_of_birth: $("cpInputDOB").value || null,
                    ssn_last4: $("cpInputSSN").value.trim() || null,
                    email: $("cpInputEmail").value.trim() || null,
                    phone: $("cpInputPhone").value.trim() || null }};
  if (!payload.full_name) {{ alert("Full name is required."); return; }}
  await fetch('/api/creditpull/subjects', {{ method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify(payload) }});
  ['cpInputName','cpInputDOB','cpInputSSN','cpInputEmail','cpInputPhone'].forEach(id => $(id).value = '');
  showView("cpSubjectList"); loadSubjects();
}}

function openPullForm(id, name) {{
  currentSubjectId = id; isRetryingCreds = false; hide($("cpPullFormError"));
  $("cpPullSubjectName").textContent = name;
  $("cpInputIIQUsername").value = ''; $("cpInputIIQPassword").value = '';
  showView("cpPullForm");
}}

async function startPull() {{
  if (isRetryingCreds) return submitRetryCredentials();
  const u = $("cpInputIIQUsername").value.trim(); const p = $("cpInputIIQPassword").value;
  if (!u || !p) {{ alert("Username and password required."); return; }}
  const r = await fetch('/api/creditpull/run', {{ method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{ subject_id: currentSubjectId, iiq_username: u, iiq_password: p }}) }});
  $("cpInputIIQUsername").value = ''; $("cpInputIIQPassword").value = '';
  const data = await r.json(); currentReportId = data.report_id;
  $("cpStatusSubjectName").textContent = $("cpPullSubjectName").textContent;
  $("cpStatusLabel").textContent = "Starting pull...";
  hide($("cpSecurityQForm")); showView("cpStatusView"); startPolling();
}}

async function submitRetryCredentials() {{
  const u = $("cpInputIIQUsername").value.trim(); const p = $("cpInputIIQPassword").value;
  if (!u || !p) {{ alert("Username and password required."); return; }}
  await fetch('/api/creditpull/credentials/' + currentReportId, {{ method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{ iiq_username: u, iiq_password: p }}) }});
  $("cpInputIIQUsername").value = ''; $("cpInputIIQPassword").value = '';
  hide($("cpPullFormError"));
  $("cpStatusLabel").textContent = "Retrying with new credentials...";
  showView("cpStatusView");
}}

function startPolling() {{ pollStatus(); pollIntervalId = setInterval(pollStatus, 1500); }}
function stopPolling() {{ if (pollIntervalId) {{ clearInterval(pollIntervalId); pollIntervalId = null; }} currentQuestionId = null; currentQuestionAttempt = 0; }}

async function pollStatus() {{
  if (!currentReportId) return;
  const data = await (await fetch('/api/creditpull/status/' + currentReportId)).json();

  if (data.status === 'awaiting_credentials') {{
    isRetryingCreds = true; hide($("cpSecurityQForm")); currentQuestionId = null;
    const e = $("cpPullFormError");
    e.textContent = data.error_message || "Invalid credentials. Please try again.";
    show(e);
    $("cpInputIIQUsername").value = ''; $("cpInputIIQPassword").value = '';
    showView("cpPullForm"); return;
  }}
  if (isRetryingCreds && data.status !== 'awaiting_credentials') {{
    isRetryingCreds = false; hide($("cpPullFormError"));
  }}
  if (data.status === 'awaiting_input' && data.question) {{
    const att = data.question.attempt_count || 1;
    const isNewQ = data.question.id !== currentQuestionId;
    const isRetry = !isNewQ && att > currentQuestionAttempt;
    if (isNewQ || isRetry) showSecurityQuestion(data.question, isRetry ? data.error_message : null);
    $("cpStatusLabel").textContent = "Waiting for your answer...";
  }} else {{
    hide($("cpSecurityQForm")); currentQuestionId = null; currentQuestionAttempt = 0;
    $("cpStatusLabel").textContent = "Status: " + data.status;
  }}
  if (data.status === 'done') {{
    stopPolling(); $("cpStatusLabel").textContent = "Loading report..."; loadReport(currentReportId);
  }} else if (data.status === 'failed') {{
    stopPolling();
    $("cpStatusLabel").innerHTML =
      '<div class="cp-form-error" style="margin:0">' +
      escapeStr(data.error_message || 'Pull failed (unknown error).') +
      '</div>';
  }}
}}

function showSecurityQuestion(q, errorMessage = null) {{
  currentQuestionId = q.id; currentQuestionAttempt = q.attempt_count || 1;
  $("cpSecurityQText").textContent = q.text;
  const e = $("cpSecurityQError");
  if (errorMessage) {{ e.textContent = errorMessage; show(e); }} else {{ hide(e); }}
  if (q.type === 'choice' && Array.isArray(q.options)) {{
    hide($("cpSecurityQTextInput")); show($("cpSecurityQChoiceInput"));
    $("cpSecurityQChoiceInput").innerHTML = q.options.map(o =>
      '<label><input type="radio" name="cpSecurityQChoice" value="' + escapeStr(o) + '"> ' + escapeStr(o) + '</label>').join('');
  }} else {{
    show($("cpSecurityQTextInput")); hide($("cpSecurityQChoiceInput"));
    $("cpSecurityQAnswerText").value = '';
  }}
  show($("cpSecurityQForm"));
}}

async function submitAnswer() {{
  if (!currentQuestionId) return;
  let answer;
  if (!$("cpSecurityQTextInput").classList.contains('hidden')) {{
    answer = $("cpSecurityQAnswerText").value.trim();
  }} else {{
    answer = (document.querySelector('input[name="cpSecurityQChoice"]:checked') || {{}}).value;
  }}
  if (!answer) {{ alert("Please provide an answer."); return; }}
  await fetch('/api/creditpull/answer/' + currentQuestionId, {{ method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{ answer }}) }});
  hide($("cpSecurityQForm"));
  // Keep currentQuestionId so a wrong-answer retry (same id, attempt_count++)
  // is detected as a retry and the error banner shows.
  $("cpStatusLabel").textContent = "Continuing...";
}}

async function loadReport(id) {{
  const report = await (await fetch('/api/creditpull/report/' + id)).json();
  renderReport(report); showView("cpReportView");
}}

function renderReport(report) {{
  const h = report.header || {{}};
  $("cpReportHeader").innerHTML =
    '<div class="cp-report-header">' +
    '<span><strong>Reference #:</strong> ' + escapeStr(h.reference_number||'-') + '</span>' +
    '<span><strong>Report Date:</strong> ' + escapeStr(h.report_date||'-') + '</span>' +
    '</div>';
  renderSectionTable($("cpReportPersonalInfo"), "Personal Information", report.personal_information||[]);
  renderCreditScore($("cpReportCreditScore"), report.credit_score||{{}});
  renderSectionTable($("cpReportSummary"), "Summary", report.summary||[]);
  renderAccountHistory($("cpReportAccountHistory"), report.accounts||[]);
}}

function renderSectionTable(c, title, rows) {{
  c.innerHTML = '<h4>' + escapeStr(title) + '</h4>' +
    '<table class="cp-table"><thead><tr><th></th>' +
    '<th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EX">Experian</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th></tr></thead><tbody>' +
    rows.map(r =>
      '<tr><td><strong>' + escapeStr(r.field) + '</strong></td>' +
      '<td>' + escapeStr(r.transunion ?? '-') + '</td>' +
      '<td>' + escapeStr(r.experian   ?? '-') + '</td>' +
      '<td>' + escapeStr(r.equifax    ?? '-') + '</td></tr>'
    ).join('') + '</tbody></table>';
}}

function renderCreditScore(c, cs) {{
  const sc = cs.scores || {{}}, fa = cs.risk_factors || {{}};
  c.innerHTML = '<h4>Credit Score</h4>' +
    '<table class="cp-table"><thead><tr><th></th>' +
    '<th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EX">Experian</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th></tr></thead><tbody>' +
    ['score','lender_rank','score_scale'].map(k => {{
      const label = k === 'score' ? 'Credit Score' : k === 'lender_rank' ? 'Lender Rank' : 'Score Scale';
      return '<tr><td><strong>' + label + '</strong></td>' +
        ['transunion','experian','equifax'].map(b =>
          '<td>' + escapeStr(sc[b]?.[k] ?? '-') + '</td>').join('') + '</tr>';
    }}).join('') +
    '</tbody></table>' +
    '<div class="cp-risk-factors"><strong>Risk Factors</strong>' +
    ['transunion','experian','equifax'].map(b =>
      '<div><em>' + b.charAt(0).toUpperCase()+b.slice(1) + ':</em>' +
      ((fa[b]||[]).length===0 ? '<span class="muted"> none</span>' :
       '<ul>' + (fa[b]||[]).map(f => '<li>' + escapeStr(f) + '</li>').join('') + '</ul>') + '</div>'
    ).join('') + '</div>';
}}

function renderAccountHistory(c, accounts) {{
  if (!accounts.length) {{ c.innerHTML = '<h4>Account History</h4><p class="muted">No accounts.</p>'; return; }}
  c.innerHTML = '<h4>Account History (' + accounts.length + ' account' + (accounts.length===1?'':'s') + ')</h4>' +
    accounts.map(renderAccountCard).join('');
}}

function renderAccountCard(a) {{
  const fields = a.fields || []; const ph = a.payment_history || {{transunion:{{}}, experian:{{}}, equifax:{{}}}};
  const months = collectMonths(ph);
  return '<div class="cp-account-card"><div class="cp-account-card-header">' + escapeStr(a.creditor_name||'-') + '</div>' +
    '<table class="cp-table"><thead><tr><th></th>' +
    '<th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EX">Experian</th>' +
    '<th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th></tr></thead><tbody>' +
    fields.map(f => '<tr><td><strong>' + escapeStr(f.field) + '</strong></td>' +
      '<td>' + escapeStr(f.transunion ?? '-') + '</td>' +
      '<td>' + escapeStr(f.experian   ?? '-') + '</td>' +
      '<td>' + escapeStr(f.equifax    ?? '-') + '</td></tr>').join('') + '</tbody></table>' +
    (months.length ? ('<div style="padding:8px 14px;font-weight:600;background:#f1f5f9;">Two-Year Payment History</div>' +
      '<table class="cp-table cp-payment-grid"><thead><tr><th></th>' +
      months.map(m => '<th>' + escapeStr(formatMonth(m)) + '</th>').join('') + '</tr></thead><tbody>' +
      ['transunion','experian','equifax'].map(b =>
        '<tr><td><strong>' + b.charAt(0).toUpperCase()+b.slice(1) + '</strong></td>' +
        months.map(m => {{ const st = (ph[b]&&ph[b][m]) || ''; return '<td class="' + statusClass(st) + '">' + escapeStr(st||'-') + '</td>'; }}).join('') + '</tr>'
      ).join('') + '</tbody></table>') : '') +
    '</div>';
}}

// Boot
activateCreditPull();
</script>
</body>
</html>
"""


def _load_fixture_from_db(report_id: str = None) -> tuple[str, list, str]:
    """
    Returns (fixture_json, mock_subjects, output_filename) for a DB-sourced report.
    mock_subjects is a list with the actual subject from the DB so the demo
    "Pull Report" click on that subject matches the report data shown.
    """
    sys.path.insert(0, str(PKG_ROOT.parent.parent))
    from Statements.creditpull.persistence import load_full_report
    from Statements.creditpull.tests.postgres_shim import connect

    sb = connect("creditpull_test")
    if not report_id:
        cur = sb.conn.cursor()
        cur.execute(
            "SELECT id FROM credit_reports WHERE status = 'done' "
            "ORDER BY pulled_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.close()
        if not row:
            sys.exit("No completed reports in creditpull_test. "
                     "Run test_scraper_e2e.py first (and choose 'y' to keep data).")
        report_id = row[0]

    report = load_full_report(sb, report_id)
    if not report:
        sys.exit(f"Report {report_id} not found.")

    # Fetch the subject so the demo's MOCK_SUBJECTS shows the matching person
    subj_id = report.get("subject_id")
    cur = sb.conn.cursor()
    cur.execute(
        "SELECT id, full_name, date_of_birth, ssn_last4, email "
        "FROM credit_subjects WHERE id = %s",
        (subj_id,),
    )
    row = cur.fetchone()
    cur.close()
    sb.conn.close()

    # Use the subject from the report. If the subject was anonymized
    # (e.g. 'E2E TEST SUBJECT'), prefer the name found in personal_information.
    name_from_pi = (report.get("header") or {}).get("subject_name")
    full_name = (row[1] if row else None) or name_from_pi or "Test Subject"
    if row and row[1] == "E2E TEST SUBJECT" and name_from_pi:
        full_name = name_from_pi

    mock_subjects = [{
        "id": str(subj_id) if subj_id else "s-from-db",
        "full_name": full_name,
        "date_of_birth": str(row[2]) if row and row[2] else None,
        "email": row[4] if row else None,
    }]

    return (
        json.dumps(report, indent=2, default=str),
        mock_subjects,
        f"credit_pull_demo_live_{report_id[:8]}.html",
    )


def main():
    parser = argparse.ArgumentParser(description="Generate Credit Pull UI demo.")
    parser.add_argument("--from-db", action="store_true",
                        help="Load latest 'done' report from local Postgres instead of fixture.")
    parser.add_argument("--report-id", help="Specific report ID to load from DB (implies --from-db).")
    args = parser.parse_args()

    if not CSS.exists():
        print(f"CSS not found: {CSS}")
        return

    OUT_DIR.mkdir(exist_ok=True)
    css_content = CSS.read_text()

    # Default subjects for the fixture demo
    default_subjects = [
        {"id": "s1", "full_name": "ED HEAT",  "date_of_birth": "1985-11-01", "email": "edheattest123@yahoo.com"},
        {"id": "s2", "full_name": "JANE DOE", "date_of_birth": "1990-04-15", "email": "jane@example.com"},
    ]

    # Pick data source
    if args.from_db or args.report_id:
        fixture_json, mock_subjects, out_name = _load_fixture_from_db(args.report_id)
        out_file = OUT_DIR / out_name
        source = "live DB report"
    else:
        if not FIXTURE.exists():
            print(f"Fixture not found: {FIXTURE}")
            return
        fixture_json = FIXTURE.read_text()
        mock_subjects = default_subjects
        out_file = OUT_FILE
        source = f"fixture ({FIXTURE.name})"

    html = HTML.format(
        css=css_content,
        fixture=fixture_json,
        subjects=json.dumps(mock_subjects, indent=2),
    )
    out_file.write_text(html)

    print(f"\n✓ Generated interactive demo from {source}")
    print(f"  → {out_file}")
    print(f"\nOpen in your browser:")
    print(f"  open '{out_file}'")
    print(f"\nWhat to try:")
    print("  1. Click '+ New Subject' to see the form")
    print("  2. Click 'Pull Report' on a subject to enter IIQ creds")
    print("  3. Type any username/password and click Start Pull")
    print("  4. Walk through the security question and see the report")
    print("  5. Use the scenario dropdown at top for retry/error flows")


if __name__ == "__main__":
    main()
