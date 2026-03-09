import { Background, Controls, Position, ReactFlow, type Edge, type Node, type NodeMouseHandler } from '@xyflow/react';
import { useEffect, useState } from 'react';

type WorkflowMetrics = {
  status?: string | null;
  active_node?: string | null;
  terminal_node?: string | null;
  duration_ms?: number | null;
};

type FailureMetrics = {
  primary_category?: string | null;
  subcategory?: string | null;
};

type TestingMetrics = {
  validation_strategy?: string | null;
};

type PhaseMetrics = {
  status?: string | null;
  attempts?: number | null;
  duration_ms?: number | null;
};

type EventMetrics = {
  node?: string | null;
  event_type?: string | null;
  status?: string | null;
  attempt?: number | null;
  timestamp?: string | null;
};

type LatestMetrics = {
  run_id?: string | null;
  workflow?: WorkflowMetrics;
  failures?: FailureMetrics;
  testing?: TestingMetrics;
  phases?: Record<string, PhaseMetrics>;
  execution_events?: EventMetrics[];
};

type MonitorRow = {
  run_id?: string;
  status?: string;
  primary_failure?: string;
  failure_subcategory?: string;
  validation_strategy?: string;
  duration_ms?: number;
  path?: string;
};

type MonitorResponse = {
  workspace_dir?: string;
  latest?: LatestMetrics;
  latest_path?: string | null;
  phase_details?: Record<string, PhaseDetail>;
  trend?: {
    run_count?: number;
    success_rate?: number;
  };
  rows?: MonitorRow[];
  generated_at?: string;
};

type PhaseDetail = {
  title?: string;
  narrative?: string;
  inputs?: string[];
  outputs?: string[];
  highlights?: string[];
  images?: PhaseImage[];
};

type PhaseImage = {
  path?: string;
  title?: string;
  caption?: string;
  url?: string;
};

const phaseNames = ['plan', 'code', 'test', 'review', 'create_pr'];
const defaultRepo = 'D:\\work\\next-test-agent-live-issue7-rerun4';

function initialQueryState(): { repo: string; recent: string } {
  if (typeof window === 'undefined') {
    return { repo: defaultRepo, recent: '5' };
  }
  const params = new URLSearchParams(window.location.search);
  const repo = params.get('repo')?.trim() || defaultRepo;
  const recent = params.get('recent')?.trim() || '5';
  return { repo, recent };
}

const phaseTitles: Record<string, string> = {
  plan: 'Planner agent',
  code: 'Coder agent',
  test: 'Tester agent',
  review: 'Reviewer agent',
  create_pr: 'Publish step',
};

function formatDuration(value?: number | null): string {
  if (!value || value <= 0) {
    return 'n/a';
  }
  if (value >= 60000) {
    return `${(value / 60000).toFixed(1)} min`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)} s`;
  }
  return `${Math.round(value)} ms`;
}

function formatRate(value?: number | null): string {
  if (value === undefined || value === null) {
    return 'none';
  }
  if (value <= 1) {
    return `${Math.round(value * 100)}%`;
  }
  return `${Math.round(value)}%`;
}

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return 'unknown';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function asText(value?: string | number | null): string {
  if (value === undefined || value === null || value === '') {
    return 'none';
  }
  return String(value);
}

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/$/, '')}${path}`;
}

function resolveAssetUrl(apiBase: string, assetUrl?: string): string | undefined {
  if (!assetUrl) {
    return undefined;
  }
  if (/^https?:\/\//i.test(assetUrl)) {
    return assetUrl;
  }
  return joinUrl(apiBase, assetUrl.startsWith('/') ? assetUrl : `/${assetUrl}`);
}

function statusTone(status?: string | null): string {
  const normalized = (status || '').toLowerCase();
  if (['approved', 'passed', 'completed', 'running', 'in_progress', 'existing'].includes(normalized)) {
    return 'good';
  }
  if (['failed', 'aborted', 'changes_required'].includes(normalized)) {
    return 'bad';
  }
  if (['not_run', 'pending', 'unknown'].includes(normalized)) {
    return 'muted';
  }
  return 'info';
}

function phaseSummary(phaseName: string, status: string, workflow: WorkflowMetrics, failures: FailureMetrics, testing: TestingMetrics): string {
  if (phaseName === 'plan') {
    return `The planner reads the issue, profiles the workspace, and decides which files or intents the rest of the run should focus on. Current planner status is ${status}.`;
  }
  if (phaseName === 'code') {
    return `The coder applies the planned edits or deterministic scaffolding. This tells you whether implementation work has started, completed, or is still waiting.`;
  }
  if (phaseName === 'test') {
    return `The tester validates the changes with the ${asText(testing.validation_strategy || 'full')} strategy. If this phase fails, the failure taxonomy usually explains what validation broke.`;
  }
  if (phaseName === 'review') {
    return `The reviewer checks changed areas and validation evidence, then decides whether the run is approved or should loop back for remediation.`;
  }
  if (phaseName === 'create_pr') {
    return `The publish step links the result back to git hosting. Terminal node ${asText(workflow.terminal_node)} and workflow status ${asText(workflow.status)} show whether the run finished cleanly.`;
  }
  return `Current workflow failure taxonomy is ${asText(failures.primary_category)}/${asText(failures.subcategory)}.`;
}

function phaseHighlights(phaseName: string, status: string, workflow: WorkflowMetrics, failures: FailureMetrics, testing: TestingMetrics): string[] {
  if (phaseName === 'plan') {
    return [
      'Reads issue context and repository profile.',
      'Chooses files, focus areas, and edit intent for downstream agents.',
      `Active workflow node is ${asText(workflow.active_node || workflow.terminal_node)}.`,
    ];
  }
  if (phaseName === 'code') {
    return [
      'Turns the plan into actual code changes.',
      'Can use deterministic framework-aware scaffolding when available.',
      `Current workflow status is ${asText(workflow.status)}.`,
    ];
  }
  if (phaseName === 'test') {
    return [
      `Validation mode is ${asText(testing.validation_strategy || 'full')}.`,
      'Checks whether the workspace still builds, types, or passes smoke validation.',
      `Latest failure taxonomy is ${asText(failures.primary_category)}/${asText(failures.subcategory)}.`,
    ];
  }
  if (phaseName === 'review') {
    return [
      'Summarizes changed areas and validation outcomes.',
      'Approves the run or sends it back for another remediation pass.',
      `Latest workflow status is ${asText(workflow.status)}.`,
    ];
  }
  if (phaseName === 'create_pr') {
    return [
      'Creates or reuses the PR after approval.',
      'Publishes the final outcome back to the hosting provider.',
      `Terminal node is ${asText(workflow.terminal_node)}.`,
    ];
  }
  return [`Current phase status is ${status}.`];
}

function App() {
  const apiBase = (import.meta.env.VITE_MONITOR_API_BASE as string | undefined) || 'http://127.0.0.1:8000';
  const initialQuery = initialQueryState();
  const [repoInput, setRepoInput] = useState(initialQuery.repo);
  const [recentInput, setRecentInput] = useState(initialQuery.recent);
  const [repo, setRepo] = useState(initialQuery.repo);
  const [recent, setRecent] = useState(initialQuery.recent);
  const [data, setData] = useState<MonitorResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedPhase, setSelectedPhase] = useState<string>('plan');

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    const params = new URLSearchParams();
    if (repo.trim()) {
      params.set('repo', repo.trim());
    }
    params.set('recent', recent || '5');
    const nextQuery = params.toString();
    const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}`;
    window.history.replaceState(null, '', nextUrl);
  }, [recent, repo]);

  useEffect(() => {
    let active = true;

    async function loadMonitor() {
      try {
        const query = new URLSearchParams();
        if (repo.trim()) {
          query.set('repo', repo.trim());
        }
        query.set('recent', recent || '5');
        const response = await fetch(joinUrl(apiBase, `/api/monitor?${query.toString()}`));
        if (!response.ok) {
          throw new Error(`Monitor API returned ${response.status}`);
        }
        const payload = (await response.json()) as MonitorResponse;
        if (!active) {
          return;
        }
        setData(payload);
        setError(null);
      } catch (fetchError) {
        if (!active) {
          return;
        }
        const message = fetchError instanceof Error ? fetchError.message : 'Unknown monitor fetch failure';
        setError(message);
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    loadMonitor();
    const timer = window.setInterval(loadMonitor, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [apiBase, recent, repo]);

  const latest = data?.latest || {};
  const workflow = latest.workflow || {};
  const failures = latest.failures || {};
  const testing = latest.testing || {};
  const phaseMetrics = latest.phases || {};
  const phaseDetails = data?.phase_details || {};
  const activeNode = workflow.active_node || workflow.terminal_node;

  useEffect(() => {
    setSelectedPhase((current) => {
      if (current && phaseNames.includes(current)) {
        return current;
      }
      return (activeNode && phaseNames.includes(activeNode)) ? activeNode : 'plan';
    });
  }, [activeNode, latest.run_id]);

  const nodes: Node[] = phaseNames.map((phaseName, index) => {
    const phase = phaseMetrics[phaseName] || {};
    const status = asText(phase.status || 'not_run');
    const isActive = activeNode === phaseName;
    const isSelected = selectedPhase === phaseName;
    const imageCount = (phaseDetails[phaseName]?.images || []).length;
    return {
      id: phaseName,
      position: { x: index * 280, y: 100 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: false,
      selectable: false,
      data: {
        label: (
          <div className={`flow-node ${statusTone(status)}${isActive ? ' active' : ''}${isSelected ? ' selected' : ''}`}>
            <div className="flow-node__top">
              <span className="flow-node__name">{phaseName}</span>
              <span className="flow-node__index">{index + 1}</span>
            </div>
            <div className="flow-node__status">{status}</div>
            <div className="flow-node__meta">Attempts {asText(phase.attempts)}</div>
            <div className="flow-node__meta">Duration {formatDuration(phase.duration_ms)}</div>
            {imageCount ? <div className="flow-node__meta">Images {imageCount}</div> : null}
          </div>
        ),
      },
      style: {
        background: 'transparent',
        border: 'none',
        width: 228,
        padding: 0,
        boxShadow: 'none',
      },
    } satisfies Node;
  });

  const edges: Edge[] = phaseNames.slice(0, -1).map((phaseName, index) => {
    const target = phaseNames[index + 1];
    const isTraversed = phaseName === activeNode || target === activeNode || index < phaseNames.indexOf(activeNode || '');
    return {
      id: `${phaseName}-${target}`,
      source: phaseName,
      target,
      animated: isTraversed,
      style: {
        stroke: isTraversed ? '#58a6ff' : 'rgba(151, 170, 196, 0.35)',
        strokeWidth: isTraversed ? 2.6 : 1.7,
      },
    } satisfies Edge;
  });

  const recentRuns = data?.rows || [];
  const events = latest.execution_events || [];
  const selectedPhaseName = phaseNames.includes(selectedPhase) ? selectedPhase : (activeNode && phaseNames.includes(activeNode) ? activeNode : 'plan');
  const selectedPhaseMetrics = phaseMetrics[selectedPhaseName] || {};
  const selectedPhaseStatus = asText(selectedPhaseMetrics.status || 'not_run');
  const selectedPhaseEvents = events.filter((event) => event.node === selectedPhaseName).slice().reverse();
  const selectedPhaseDetail = phaseDetails[selectedPhaseName] || {};
  const selectedPhaseNarrative = selectedPhaseDetail.narrative || phaseSummary(selectedPhaseName, selectedPhaseStatus, workflow, failures, testing);
  const selectedPhaseHighlights = (selectedPhaseDetail.highlights && selectedPhaseDetail.highlights.length)
    ? selectedPhaseDetail.highlights
    : phaseHighlights(selectedPhaseName, selectedPhaseStatus, workflow, failures, testing);
  const selectedPhaseInputs = selectedPhaseDetail.inputs || [];
  const selectedPhaseOutputs = selectedPhaseDetail.outputs || [];
  const selectedPhaseImages = selectedPhaseDetail.images || [];

  const handleNodeClick: NodeMouseHandler = (_event, node) => {
    setSelectedPhase(node.id);
  };

  return (
    <div className="app-shell">
      <header className="hero-panel">
        <div className="hero-copy">
          <div className="hero-kicker">Monitor frontend service</div>
          <div className="hero-title-wrap">
            <div className="eyebrow">ReactFlow monitor</div>
            <h1>Execution graph for AI Code Agent runs.</h1>
          </div>
          <p className="hero-description">
            Dedicated frontend service for monitor telemetry. It renders the current run as a flow graph while reusing the
            existing backend monitor API.
          </p>
          <div className="hero-pills">
            <span className="hero-pill">Backend {apiBase}</span>
            <span className="hero-pill">Workspace {asText(data?.workspace_dir || repo)}</span>
            <span className="hero-pill">Generated {formatTimestamp(data?.generated_at)}</span>
          </div>
        </div>
        <form className="query-panel" onSubmit={(event) => {
          event.preventDefault();
          setRepo(repoInput);
          setRecent(recentInput || '5');
          setLoading(true);
        }}>
          <div className="query-header">
            <div>
              <div className="eyebrow">Query</div>
              <h2>Monitor scope</h2>
            </div>
            <div className="micro-copy">Auto-refresh 2s</div>
          </div>
          <label className="field">
            <span>Workspace path</span>
            <input value={repoInput} onChange={(event) => setRepoInput(event.target.value)} placeholder="D:\\work\\next-test-agent-live" />
          </label>
          <label className="field">
            <span>Recent runs</span>
            <input value={recentInput} onChange={(event) => setRecentInput(event.target.value)} type="number" min="1" max="20" />
          </label>
          <button className="apply-button" type="submit">Apply query</button>
        </form>
      </header>

      <section className="metric-grid">
        <article className="metric-card">
          <div className="metric-label">Latest status</div>
          <div className={`metric-value status-${statusTone(workflow.status)}`}>{asText(workflow.status)}</div>
          <p className="metric-note">Current run anchored on {asText(workflow.active_node || workflow.terminal_node)}.</p>
        </article>
        <article className="metric-card">
          <div className="metric-label">Validation strategy</div>
          <div className="metric-value">{asText(testing.validation_strategy || 'full')}</div>
          <p className="metric-note">Recent success rate {formatRate(data?.trend?.success_rate)}.</p>
        </article>
        <article className="metric-card">
          <div className="metric-label">Latest failure</div>
          <div className="metric-value">{asText(failures.subcategory || failures.primary_category)}</div>
          <p className="metric-note">Duration {formatDuration(workflow.duration_ms)}.</p>
        </article>
      </section>

      <main className="content-grid">
        <section className="surface-panel flow-panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Current run</div>
              <h2>{latest.run_id ? `Run ${latest.run_id}` : 'No run artifacts found'}</h2>
            </div>
            <div className={`status-chip status-${statusTone(workflow.status)}`}>{asText(workflow.status || (loading ? 'loading' : 'unknown'))}</div>
          </div>
          <p className="panel-summary">
            Latest artifact {asText(data?.latest_path)}. Active node {asText(activeNode)}. Failure taxonomy {asText(failures.primary_category)}/
            {asText(failures.subcategory)}.
          </p>
          <div className="flow-canvas">
            <ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.18 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} panOnDrag={false} zoomOnScroll={false} zoomOnPinch={false} zoomOnDoubleClick={false} onNodeClick={handleNodeClick}>
              <Background color="rgba(120, 144, 174, 0.24)" gap={20} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
          <section className="agent-detail-panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Selected step</div>
                <h2>{selectedPhaseDetail.title || phaseTitles[selectedPhaseName] || selectedPhaseName}</h2>
              </div>
              <div className={`status-chip status-${statusTone(selectedPhaseStatus)}`}>{selectedPhaseStatus}</div>
            </div>
            <p className="panel-summary">{selectedPhaseNarrative}</p>
            <div className="detail-grid">
              <article className="detail-card">
                <div className="detail-label">What this agent is doing</div>
                <ul className="detail-list">
                  {selectedPhaseHighlights.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </article>
              <article className="detail-card">
                <div className="detail-label">Execution snapshot</div>
                <div className="detail-kv"><span>Status</span><strong>{selectedPhaseStatus}</strong></div>
                <div className="detail-kv"><span>Attempts</span><strong>{asText(selectedPhaseMetrics.attempts)}</strong></div>
                <div className="detail-kv"><span>Duration</span><strong>{formatDuration(selectedPhaseMetrics.duration_ms)}</strong></div>
                <div className="detail-kv"><span>Workflow node</span><strong>{asText(activeNode)}</strong></div>
              </article>
            </div>
            <div className="detail-grid">
              <article className="detail-card">
                <div className="detail-label">Inputs used by this step</div>
                {selectedPhaseInputs.length ? (
                  <ul className="detail-list">
                    {selectedPhaseInputs.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                ) : <div className="empty-state">No summarized inputs captured for this step yet.</div>}
              </article>
              <article className="detail-card">
                <div className="detail-label">Outputs produced by this step</div>
                {selectedPhaseOutputs.length ? (
                  <ul className="detail-list">
                    {selectedPhaseOutputs.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                ) : <div className="empty-state">No summarized outputs captured for this step yet.</div>}
              </article>
            </div>
            <article className="detail-card detail-card--full">
              <div className="detail-label">Images for this step</div>
              {selectedPhaseImages.length ? (
                <div className="image-grid">
                  {selectedPhaseImages.map((image) => (
                    <figure className="image-card" key={`${image.path}-${image.url}`}>
                      <img alt={image.title || image.path || 'Monitor artifact'} src={resolveAssetUrl(apiBase, image.url)} />
                      <figcaption>
                        <strong>{image.title || image.path || 'Screenshot artifact'}</strong>
                        <span>{image.caption || image.path || 'Visual review artifact'}</span>
                      </figcaption>
                    </figure>
                  ))}
                </div>
              ) : <div className="empty-state">No images were captured for this step.</div>}
            </article>
            <div className="detail-events">
              <div className="detail-label">Events for this step</div>
              <div className="detail-event-list">
                {selectedPhaseEvents.length ? selectedPhaseEvents.map((event, index) => (
                  <article className="detail-event-card" key={`${event.node}-${event.event_type}-${event.timestamp}-${index}`}>
                    <div className="list-title-row">
                      <strong>{asText(event.event_type)}</strong>
                      <span className={`status-chip status-${statusTone(event.status)}`}>{asText(event.status)}</span>
                    </div>
                    <div className="tag-row">
                      <span className="tag">Attempt {asText(event.attempt)}</span>
                      <span className="tag">{formatTimestamp(event.timestamp)}</span>
                    </div>
                  </article>
                )) : <div className="empty-state">No persisted events yet for this step.</div>}
              </div>
            </div>
          </section>
          {error ? <div className="notice error">{error}</div> : null}
          {!error && loading ? <div className="notice">Loading monitor data...</div> : null}
        </section>

        <aside className="sidebar-grid">
          <section className="surface-panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Execution events</div>
                <h2>Timeline</h2>
              </div>
              <div className="micro-copy">Newest first</div>
            </div>
            <div className="stack-list">
              {events.length ? events.slice().reverse().map((event, index) => (
                <article className="list-card" key={`${event.node}-${event.event_type}-${event.timestamp}-${index}`}>
                  <div className="list-title-row">
                    <strong>{asText(event.node)} · {asText(event.event_type)}</strong>
                    <span className={`status-chip status-${statusTone(event.status)}`}>{asText(event.status)}</span>
                  </div>
                  <div className="tag-row">
                    <span className="tag">Attempt {asText(event.attempt)}</span>
                    <span className="tag">{formatTimestamp(event.timestamp)}</span>
                  </div>
                </article>
              )) : <div className="empty-state">No execution events persisted yet.</div>}
            </div>
          </section>

          <section className="surface-panel">
            <div className="panel-header">
              <div>
                <div className="eyebrow">Recent runs</div>
                <h2>Portfolio</h2>
              </div>
              <div className="micro-copy">{recentRuns.length} runs</div>
            </div>
            <div className="stack-list">
              {recentRuns.length ? recentRuns.map((row) => (
                <article className="list-card" key={row.run_id}>
                  <div className="list-title-row">
                    <strong>{asText(row.run_id)}</strong>
                    <span className={`status-chip status-${statusTone(row.status)}`}>{asText(row.status)}</span>
                  </div>
                  <div className="tag-row">
                    <span className="tag">{asText(row.validation_strategy)}</span>
                    <span className="tag">{formatDuration(row.duration_ms)}</span>
                  </div>
                  <div className="list-meta">Failure {asText(row.failure_subcategory || row.primary_failure)}</div>
                  <div className="list-path">{asText(row.path)}</div>
                </article>
              )) : <div className="empty-state">No recent runs found.</div>}
            </div>
          </section>
        </aside>
      </main>
    </div>
  );
}

export default App;
