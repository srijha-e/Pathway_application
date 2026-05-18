"""
Generate a self-contained HTML page that renders the saved credit-report
fixture using the SAME CSS and rendering logic as the CRM. Open the
resulting file in your browser to visually verify the UI exactly matches
how a real scraped report would display.

Run:
    ./venv/bin/python Statements/creditpull/tools/preview_report_ui.py

Then open the printed file path in your browser.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent                              # Statements/creditpull
PUB_ROOT = PKG_ROOT.parent / "pub"                  # Statements/pub
FIXTURE  = PKG_ROOT / "fixtures" / "sample_parsed_report.json"
CSS      = PUB_ROOT / "css" / "creditpull.css"
JS_MOD   = PUB_ROOT / "js" / "modules" / "creditpull.js"

OUT_DIR  = HERE / "_preview"
OUT_FILE = OUT_DIR / "report.html"


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Credit Pull — UI preview</title>
  <style>
    /* ── minimal global CSS so the preview looks like the CRM main area ── */
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      background: #f8fafc;
      margin: 0;
      padding: 24px;
      color: #1e293b;
    }}
    .card {{
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.05);
      padding: 24px;
      margin: 0 auto;
      max-width: 1200px;
    }}
    .row-tight {{ display: flex; justify-content: space-between; align-items: center; }}
    .muted     {{ color: #64748b; }}
    button.secondary {{
      background: transparent; border: 1px solid #cbd5e1; color: #475569;
      padding: 6px 12px; border-radius: 4px; cursor: pointer;
    }}
    .hidden {{ display: none !important; }}
    /* Banner at top — preview-only */
    .preview-banner {{
      background: #fef3c7; border: 1px solid #fbbf24; color: #92400e;
      padding: 10px 14px; border-radius: 6px; margin-bottom: 16px;
      max-width: 1200px; margin-left: auto; margin-right: auto;
    }}

    /* ── Inlined production CSS (creditpull.css) ── */
{css}
  </style>
</head>
<body>

  <div class="preview-banner">
    <strong>UI Preview</strong> — rendering fixture data exactly as the CRM would.
    No backend, no scraping. If this looks right, the production CRM will look the same.
  </div>

  <!-- This is the same DOM the CRM uses for cpReportView -->
  <div class="card">
    <div class="row-tight" style="margin-bottom:8px;">
      <h3 style="margin:0">Credit Report</h3>
      <button class="secondary">← Back to Subjects</button>
    </div>
    <div id="cpReportHeader"></div>
    <div id="cpReportPersonalInfo"   class="cp-section"></div>
    <div id="cpReportCreditScore"    class="cp-section"></div>
    <div id="cpReportSummary"        class="cp-section"></div>
    <div id="cpReportAccountHistory" class="cp-section"></div>
  </div>

  <script>
    // ── Inlined fixture data ───────────────────────────────────────────
    const REPORT = {fixture};

    // Tiny helpers (mirroring utils.js)
    const $ = (id) => document.getElementById(id);
    const show = (el) => el?.classList.remove("hidden");
    const hide = (el) => el?.classList.add("hidden");

    function escape(s) {{
      if (s == null) return '';
      return String(s).replace(/[&<>"']/g, c => (
        {{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[c]
      ));
    }}

    function statusClass(status) {{
      if (!status) return '';
      const s = String(status).toUpperCase();
      if (s === 'OK') return 'ok';
      if (['30', '60', '90', '120'].includes(s)) return 'late';
      if (['CO', 'CHARGEOFF'].includes(s)) return 'bad';
      return '';
    }}

    function formatMonth(ym) {{
      const m = /^(\\d{{4}})-(\\d{{2}})$/.exec(ym);
      if (!m) return ym;
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      return `${{months[parseInt(m[2], 10) - 1]}} ${{m[1].slice(2)}}`;
    }}

    function collectMonths(ph) {{
      const set = new Set();
      ['transunion','experian','equifax'].forEach(b => {{
        Object.keys(ph[b] || {{}}).forEach(m => set.add(m));
      }});
      return [...set].sort().reverse();
    }}

    // ── Rendering functions (copied from creditpull.js, no behavioural changes) ──
    function renderReport(report) {{
      const h = report.header || {{}};
      $("cpReportHeader").innerHTML = `
        <div class="cp-report-header">
          <span><strong>Reference #:</strong> ${{escape(h.reference_number || '-')}}</span>
          <span><strong>Report Date:</strong> ${{escape(h.report_date || '-')}}</span>
        </div>
      `;
      renderSectionTable($("cpReportPersonalInfo"), "Personal Information", report.personal_information || []);
      renderCreditScore($("cpReportCreditScore"), report.credit_score || {{}});
      renderSectionTable($("cpReportSummary"), "Summary", report.summary || []);
      renderAccountHistory($("cpReportAccountHistory"), report.accounts || []);
    }}

    function renderSectionTable(container, title, rows) {{
      container.innerHTML = `
        <h4>${{escape(title)}}</h4>
        <table class="cp-table">
          <thead>
            <tr>
              <th></th>
              <th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>
              <th class="cp-bureau-th cp-bureau-th-EX">Experian</th>
              <th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th>
            </tr>
          </thead>
          <tbody>
            ${{rows.map(r => `
              <tr>
                <td><strong>${{escape(r.field)}}</strong></td>
                <td>${{escape(r.transunion ?? '-')}}</td>
                <td>${{escape(r.experian   ?? '-')}}</td>
                <td>${{escape(r.equifax    ?? '-')}}</td>
              </tr>
            `).join('')}}
          </tbody>
        </table>
      `;
    }}

    function renderCreditScore(container, cs) {{
      const scores = cs.scores || {{}};
      const factors = cs.risk_factors || {{}};
      container.innerHTML = `
        <h4>Credit Score</h4>
        <table class="cp-table">
          <thead>
            <tr>
              <th></th>
              <th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>
              <th class="cp-bureau-th cp-bureau-th-EX">Experian</th>
              <th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Credit Score</strong></td>
              <td>${{escape(scores.transunion?.score ?? '-')}}</td>
              <td>${{escape(scores.experian?.score   ?? '-')}}</td>
              <td>${{escape(scores.equifax?.score    ?? '-')}}</td>
            </tr>
            <tr>
              <td><strong>Lender Rank</strong></td>
              <td>${{escape(scores.transunion?.lender_rank ?? '-')}}</td>
              <td>${{escape(scores.experian?.lender_rank   ?? '-')}}</td>
              <td>${{escape(scores.equifax?.lender_rank    ?? '-')}}</td>
            </tr>
            <tr>
              <td><strong>Score Scale</strong></td>
              <td>${{escape(scores.transunion?.score_scale ?? '-')}}</td>
              <td>${{escape(scores.experian?.score_scale   ?? '-')}}</td>
              <td>${{escape(scores.equifax?.score_scale    ?? '-')}}</td>
            </tr>
          </tbody>
        </table>
        <div class="cp-risk-factors">
          <strong>Risk Factors</strong>
          ${{['transunion', 'experian', 'equifax'].map(b => `
            <div>
              <em>${{b.charAt(0).toUpperCase() + b.slice(1)}}:</em>
              ${{(factors[b] || []).length === 0
                ? '<span class="muted"> none</span>'
                : '<ul>' + (factors[b] || []).map(f => `<li>${{escape(f)}}</li>`).join('') + '</ul>'}}
            </div>
          `).join('')}}
        </div>
      `;
    }}

    function renderAccountHistory(container, accounts) {{
      if (accounts.length === 0) {{
        container.innerHTML = '<h4>Account History</h4><p class="muted">No accounts.</p>';
        return;
      }}
      container.innerHTML = `
        <h4>Account History (${{accounts.length}} account${{accounts.length === 1 ? '' : 's'}})</h4>
        ${{accounts.map(renderAccountCard).join('')}}
      `;
    }}

    function renderAccountCard(account) {{
      const fields = account.fields || [];
      const ph = account.payment_history || {{ transunion: {{}}, experian: {{}}, equifax: {{}} }};
      const months = collectMonths(ph);
      return `
        <div class="cp-account-card">
          <div class="cp-account-card-header">${{escape(account.creditor_name || '-')}}</div>
          <table class="cp-table">
            <thead>
              <tr>
                <th></th>
                <th class="cp-bureau-th cp-bureau-th-TU">TransUnion</th>
                <th class="cp-bureau-th cp-bureau-th-EX">Experian</th>
                <th class="cp-bureau-th cp-bureau-th-EQ">Equifax</th>
              </tr>
            </thead>
            <tbody>
              ${{fields.map(f => `
                <tr>
                  <td><strong>${{escape(f.field)}}</strong></td>
                  <td>${{escape(f.transunion ?? '-')}}</td>
                  <td>${{escape(f.experian   ?? '-')}}</td>
                  <td>${{escape(f.equifax    ?? '-')}}</td>
                </tr>
              `).join('')}}
            </tbody>
          </table>
          ${{months.length > 0 ? `
            <div style="padding: 8px 14px; font-weight:600; background:#f1f5f9;">Two-Year Payment History</div>
            <table class="cp-table cp-payment-grid">
              <thead>
                <tr>
                  <th></th>
                  ${{months.map(m => `<th>${{escape(formatMonth(m))}}</th>`).join('')}}
                </tr>
              </thead>
              <tbody>
                ${{['transunion', 'experian', 'equifax'].map(b => `
                  <tr>
                    <td><strong>${{b.charAt(0).toUpperCase() + b.slice(1)}}</strong></td>
                    ${{months.map(m => {{
                      const status = (ph[b] && ph[b][m]) || '';
                      return `<td class="${{statusClass(status)}}">${{escape(status || '-')}}</td>`;
                    }}).join('')}}
                  </tr>
                `).join('')}}
              </tbody>
            </table>
          ` : ''}}
        </div>
      `;
    }}

    // ── Fire it ───────────────────────────────────────────────────
    renderReport(REPORT);
  </script>
</body>
</html>
"""


def main():
    if not FIXTURE.exists():
        print(f"Fixture not found: {FIXTURE}")
        return
    if not CSS.exists():
        print(f"CSS not found: {CSS}")
        return

    OUT_DIR.mkdir(exist_ok=True)
    fixture_json = FIXTURE.read_text()
    css_content  = CSS.read_text()

    html = HTML_TEMPLATE.format(css=css_content, fixture=fixture_json)
    OUT_FILE.write_text(html)

    print(f"\n✓ Generated preview: {OUT_FILE}")
    print(f"\nOpen in your browser:")
    print(f"  open '{OUT_FILE}'")
    print(f"\nOr just double-click the file in Finder. No server needed.")


if __name__ == "__main__":
    main()
