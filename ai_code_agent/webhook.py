try:
    import uvicorn
    from fastapi import FastAPI, Query, Request
    from fastapi.responses import HTMLResponse
except ImportError:  # pragma: no cover - optional dependency
    uvicorn = None
    FastAPI = None
    Query = None
    Request = None
    HTMLResponse = None

from ai_code_agent.config import AgentConfig
from ai_code_agent.metrics import build_execution_metrics_trend, list_execution_metrics_artifacts, utc_now_iso


MONITOR_HTML = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>AI Code Agent Monitor</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f4efe6;
        --panel: rgba(255, 251, 245, 0.88);
        --panel-strong: #fffaf2;
        --ink: #1f2a22;
        --muted: #57645b;
        --line: rgba(73, 84, 72, 0.14);
        --accent: #236a4b;
        --accent-soft: #dff4e8;
        --bad-soft: #fde1df;
        --shadow: 0 18px 50px rgba(52, 53, 45, 0.12);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: Georgia, \"Times New Roman\", serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(35, 106, 75, 0.12), transparent 28%),
          radial-gradient(circle at top right, rgba(166, 93, 27, 0.10), transparent 24%),
          linear-gradient(180deg, #fbf6ee 0%, var(--bg) 100%);
      }
      .shell {
        max-width: 1280px;
        margin: 0 auto;
        padding: 32px 20px 56px;
      }
      .hero, .panel {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 28px;
        box-shadow: var(--shadow);
      }
      .hero {
        padding: 28px;
        display: grid;
        gap: 20px;
      }
      .hero-grid, .stats, .phase-grid, .content-grid {
        display: grid;
        gap: 16px;
      }
      .hero-grid { grid-template-columns: 2fr 1fr; }
      .stats { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .content-grid { margin-top: 20px; grid-template-columns: 1.2fr 0.8fr; }
      .phase-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
      .eyebrow {
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 12px;
        color: var(--muted);
      }
      h1, h2, h3, p { margin: 0; }
      h1 { font-size: clamp(30px, 4vw, 52px); line-height: 0.98; max-width: 12ch; }
      .card, .phase, .timeline-item, .run-row {
        background: var(--panel-strong);
        border: 1px solid var(--line);
        border-radius: 18px;
      }
      .card { padding: 18px; }
      .phase, .timeline-item, .run-row { padding: 14px; }
      .phase.running, .badge.running, .badge.approved, .phase.completed, .phase.approved, .phase.passed {
        background: var(--accent-soft);
      }
      .phase.failed, .phase.changes_required, .badge.failed, .badge.aborted, .badge.changes_required {
        background: var(--bad-soft);
      }
      .phase.not_run { opacity: 0.66; }
      .stat-label, .timeline-meta, .run-meta, .footer-note, label {
        color: var(--muted);
        font-size: 13px;
      }
      .stat-value { font-size: 28px; line-height: 1; margin-top: 8px; }
      .panel { padding: 20px; }
      .controls {
        display: grid;
        gap: 10px;
        align-content: start;
      }
      input {
        width: 100%;
        border-radius: 14px;
        border: 1px solid var(--line);
        background: var(--panel-strong);
        padding: 12px 14px;
        font: inherit;
        color: var(--ink);
      }
      button {
        border: none;
        border-radius: 999px;
        padding: 12px 16px;
        background: var(--accent);
        color: white;
        font: inherit;
        cursor: pointer;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        border-radius: 999px;
        background: var(--panel-strong);
        border: 1px solid var(--line);
        font-size: 14px;
      }
      .timeline, .runs { display: grid; gap: 12px; margin-top: 16px; }
      .empty {
        padding: 28px;
        text-align: center;
        color: var(--muted);
        border: 1px dashed var(--line);
        border-radius: 18px;
      }
      @media (max-width: 980px) {
        .hero-grid, .stats, .content-grid, .phase-grid { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <main class=\"shell\">
      <section class=\"hero\">
        <div class=\"hero-grid\">
          <div>
            <div class=\"eyebrow\">Live Monitor</div>
            <h1>AI Code Agent run status in near real time</h1>
            <p class=\"footer-note\">Node-level progress is persisted as each phase starts and completes. This page refreshes every 2 seconds.</p>
          </div>
          <form class=\"controls\" id=\"controls\">
            <div>
              <label for=\"repo\">Workspace path</label>
              <input id=\"repo\" name=\"repo\" placeholder=\"D:\\\\work\\\\next-test-agent-live\" />
            </div>
            <div>
              <label for=\"recent\">Recent runs</label>
              <input id=\"recent\" name=\"recent\" type=\"number\" min=\"1\" max=\"20\" value=\"5\" />
            </div>
            <button type=\"submit\">Refresh monitor</button>
          </form>
        </div>
        <div class=\"stats\" id=\"stats\"></div>
      </section>
      <section class=\"content-grid\">
        <div class=\"panel\">
          <div class=\"eyebrow\">Current Run</div>
          <h2 id=\"current-title\">Waiting for data</h2>
          <p class=\"footer-note\" id=\"current-summary\">Point the monitor at a workspace with .ai-code-agent run artifacts.</p>
          <div class=\"phase-grid\" id=\"phases\"></div>
          <div class=\"timeline\" id=\"timeline\"></div>
        </div>
        <div class=\"panel\">
          <div class=\"eyebrow\">Recent Runs</div>
          <h2>Run history</h2>
          <div class=\"runs\" id=\"runs\"></div>
        </div>
      </section>
    </main>
    <script>
      const params = new URLSearchParams(window.location.search);
      const repoInput = document.getElementById('repo');
      const recentInput = document.getElementById('recent');
      const stats = document.getElementById('stats');
      const currentTitle = document.getElementById('current-title');
      const currentSummary = document.getElementById('current-summary');
      const phases = document.getElementById('phases');
      const timeline = document.getElementById('timeline');
      const runs = document.getElementById('runs');
      repoInput.value = params.get('repo') || '';
      recentInput.value = params.get('recent') || '5';

      function badgeClass(status) {
        return `badge ${status || 'unknown'}`;
      }

      function fmt(value) {
        return value === null || value === undefined || value === '' ? 'none' : String(value);
      }

      function renderStats(data) {
        const trend = data.trend || {};
        const latest = data.latest || {};
        const workflow = latest.workflow || {};
        const failures = latest.failures || {};
        stats.innerHTML = [
          ['Latest status', `<span class="${badgeClass(workflow.status)}">${fmt(workflow.status)}</span>`],
          ['Active node', fmt(workflow.active_node || workflow.terminal_node)],
          ['Latest failure', fmt(failures.subcategory || failures.primary_category)],
          ['Recent success rate', fmt(trend.success_rate)],
        ].map(([label, value]) => `<article class="card"><div class="stat-label">${label}</div><div class="stat-value">${value}</div></article>`).join('');
      }

      function renderCurrent(data) {
        const latest = data.latest || {};
        const workflow = latest.workflow || {};
        const failures = latest.failures || {};
        currentTitle.textContent = latest.run_id ? `Run ${latest.run_id}` : 'No run artifacts found';
        currentSummary.textContent = latest.run_id
          ? `Status ${fmt(workflow.status)} on ${fmt(workflow.active_node || workflow.terminal_node)}. Failure taxonomy: ${fmt(failures.primary_category)}/${fmt(failures.subcategory)}.`
          : 'Point the monitor at a workspace with .ai-code-agent run artifacts.';

        const phaseNames = ['plan', 'code', 'test', 'review', 'create_pr'];
        phases.innerHTML = phaseNames.map((name) => {
          const phase = (latest.phases || {})[name] || {};
          return `<article class="phase ${fmt(phase.status)}"><div class="eyebrow">${name}</div><h3>${fmt(phase.status)}</h3><div class="footer-note">attempts=${fmt(phase.attempts)} duration_ms=${fmt(phase.duration_ms)}</div></article>`;
        }).join('');

        const events = latest.execution_events || [];
        if (!events.length) {
          timeline.innerHTML = '<div class="empty">No execution events persisted yet.</div>';
          return;
        }
        timeline.innerHTML = events.slice().reverse().map((event) => `
          <article class="timeline-item">
            <strong>${fmt(event.node)} · ${fmt(event.status)}</strong>
            <div class="timeline-meta">${fmt(event.event_type)} | attempt ${fmt(event.attempt)} | ${fmt(event.timestamp)}</div>
          </article>
        `).join('');
      }

      function renderRuns(data) {
        const rows = data.rows || [];
        if (!rows.length) {
          runs.innerHTML = '<div class="empty">No recent runs found for this workspace.</div>';
          return;
        }
        runs.innerHTML = rows.map((row) => `
          <article class="run-row">
            <strong>${fmt(row.run_id)}</strong>
            <div class="run-meta">status=${fmt(row.status)} | failure=${fmt(row.failure_subcategory || row.primary_failure)} | strategy=${fmt(row.validation_strategy)} | duration_ms=${fmt(row.duration_ms)}</div>
          </article>
        `).join('');
      }

      async function refreshMonitor(pushUrl = false) {
        const repo = repoInput.value.trim();
        const recent = recentInput.value || '5';
        const query = new URLSearchParams();
        if (repo) query.set('repo', repo);
        if (recent) query.set('recent', recent);
        if (pushUrl) {
          const nextUrl = new URL(window.location.href);
          nextUrl.search = query.toString();
          window.history.replaceState({}, '', nextUrl);
        }
        const response = await fetch(`/api/monitor?${query.toString()}`);
        const data = await response.json();
        renderStats(data);
        renderCurrent(data);
        renderRuns(data);
      }

      document.getElementById('controls').addEventListener('submit', (event) => {
        event.preventDefault();
        refreshMonitor(true);
      });

      refreshMonitor(false);
      setInterval(() => refreshMonitor(false), 2000);
    </script>
  </body>
</html>
"""


def _monitor_payload(repo: str | None, recent: int) -> dict[str, object]:
    config = AgentConfig()
    workspace_dir = repo or config.workspace_dir
    metrics_entries = list_execution_metrics_artifacts(workspace_dir, limit=max(1, recent))
    latest_metrics = metrics_entries[0][0] if metrics_entries else {}
    latest_path = metrics_entries[0][1] if metrics_entries else None
    trend = build_execution_metrics_trend(metrics_entries)
    rows: list[dict[str, object]] = []
    for metrics, path in metrics_entries:
        workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
        failures = metrics.get("failures") if isinstance(metrics.get("failures"), dict) else {}
        testing = metrics.get("testing") if isinstance(metrics.get("testing"), dict) else {}
        rows.append(
            {
                "run_id": metrics.get("run_id") or "",
                "status": workflow.get("status") or "",
                "primary_failure": failures.get("primary_category") or "",
                "failure_subcategory": failures.get("subcategory") or "",
                "validation_strategy": testing.get("validation_strategy") or "full",
                "duration_ms": workflow.get("duration_ms") or 0,
                "path": path,
            }
        )
    return {
        "workspace_dir": workspace_dir,
        "latest": latest_metrics,
        "latest_path": latest_path,
        "trend": trend,
        "rows": rows,
        "generated_at": utc_now_iso(),
    }


if FastAPI is not None:
    app = FastAPI(title="AI Code Agent Webhook Server")

    @app.get("/monitor", response_class=HTMLResponse)
    async def monitor_page():
        """Render the run monitor UI."""
        return HTMLResponse(MONITOR_HTML)

    @app.get("/api/monitor")
    async def monitor_api(repo: str | None = Query(default=None), recent: int = Query(default=5, ge=1, le=20)):
        """Return recent run status and timeline data for the requested workspace."""
        return _monitor_payload(repo, recent)

    @app.post("/github/webhook")
    async def github_webhook(request: Request):
        """Handle incoming GitHub events."""
        payload = await request.json()
        return {"status": "received", "source": "github", "event_keys": sorted(payload.keys())}

    @app.post("/ado/webhook")
    async def ado_webhook(request: Request):
        """Handle incoming Azure DevOps service hook events."""
        payload = await request.json()
        return {"status": "received", "source": "ado", "event_keys": sorted(payload.keys())}
else:
    app = None


def start_server(host: str = "0.0.0.0", port: int = 8000):
    if uvicorn is None or app is None:
        raise RuntimeError("fastapi and uvicorn must be installed to start the webhook server")
    uvicorn.run("ai_code_agent.webhook:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    start_server()
