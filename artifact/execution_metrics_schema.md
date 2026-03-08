# Execution Metrics Schema

This document defines the first production-facing metrics schema for workflow runs.
It is designed to sit on top of the telemetry the repository already emits today:

- `execution_events` from the orchestrator
- `codegen_summary` from the coder
- `review_summary` from the reviewer
- `test_results`, `test_passed`, and `visual_review` from the tester

The design goal is to make run health, failure patterns, and retry behavior queryable without replaying raw logs.

## Goals

1. Produce one stable run-level metrics document per workflow invocation.
2. Preserve enough phase detail to explain failures and retries.
3. Derive most fields from current state so rollout does not require a large refactor.
4. Leave room for future latency, token, and cost metrics without breaking consumers.

## Non-goals

1. Replace `execution_events` as the raw audit trail.
2. Model every low-level subprocess line or LLM token immediately.
3. Require external storage before the schema is useful.

## Schema Strategy

Two related structures are defined:

1. `execution_metrics`: one aggregate record per run.
2. `execution_event_v2`: a normalized event shape for future event emission.

The repository can implement this incrementally by first deriving `execution_metrics` from the current state, then later upgrading event emission to match `execution_event_v2` exactly.

## Top-Level Run Schema

```json
{
  "schema_version": "execution-metrics/v1",
  "run_id": "20260308T102233Z-8d6f2b8f",
  "issue": {
    "mode": "change_request",
    "analysis_only": false,
    "source": "cli",
    "description": "add metrics and observability"
  },
  "workflow": {
    "status": "failed",
    "started_at": "2026-03-08T10:22:33Z",
    "completed_at": "2026-03-08T10:24:01Z",
    "duration_ms": 88000,
    "retry_count": 1,
    "attempt_count": 2,
    "terminal_node": "review",
    "created_pr": false
  },
  "workspace": {
    "path": "d:/work/agent",
    "has_python": true,
    "has_package_json": false,
    "frameworks": ["nextjs"],
    "package_manager": "npm"
  },
  "planning": {
    "retrieval_strategy": "hybrid",
    "candidate_file_count": 12,
    "graph_seed_file_count": 4,
    "blocked_file_count": 1,
    "files_to_edit_count": 3,
    "edit_intent_count": 2
  },
  "coding": {
    "generated_by": "llm",
    "requested_operations": 6,
    "applied_operations": 4,
    "failed_operation_count": 1,
    "blocked_operation_count": 1,
    "patch_count": 4,
    "changed_file_count": 3,
    "remediation_applied": true,
    "remediation_focus_count": 2
  },
  "testing": {
    "status": "failed",
    "command_count": 3,
    "failed_command_count": 1,
    "failed_commands": ["script:build"],
    "lint_issue_count": 0,
    "total_duration_ms": 48100,
    "validation_strategy": "targeted_retry",
    "selected_command_count": 1,
    "skipped_command_count": 2,
    "requested_retry_labels": ["script:build"],
    "command_reduction_rate": 0.67,
    "slowest_command": {
      "label": "script:build",
      "duration_ms": 38000,
      "exit_code": 1,
      "timed_out": false
    },
    "commands": [
      {
        "label": "compileall",
        "exit_code": 0,
        "duration_ms": 4100,
        "mode": "local",
        "timed_out": false
      },
      {
        "label": "script:build",
        "exit_code": 1,
        "duration_ms": 38000,
        "mode": "local",
        "timed_out": false
      }
    ],
    "visual_review": {
      "enabled": true,
      "screenshot_status": "passed",
      "artifact_count": 2,
      "missing_state_count": 0,
      "missing_responsive_category_count": 0
    }
  },
  "review": {
    "status": "changes_required",
    "approved": false,
    "comment_count": 3,
    "residual_risk_count": 3,
    "changed_area_count": 2,
    "validation_failed_count": 1,
    "remediation_required": true
  },
  "effectiveness": {
    "retry_attempted": true,
    "retry_recovered": false,
    "remediation_applied": true,
    "remediation_recovered": false,
    "edit_intent_used": true,
    "edit_intent_recovered": false,
    "targeted_retry_used": true,
    "command_reduction_count": 2,
    "command_reduction_rate": 0.67
  },
  "failures": {
    "has_failure": true,
    "primary_category": "validation",
    "categories": ["validation", "review"],
    "error_message": "Smoke tests failed.",
    "blocking_comment_count": 2
  },
  "phases": {
    "plan": {
      "status": "completed",
      "attempts": 1,
      "started_at": "2026-03-08T10:22:33Z",
      "completed_at": "2026-03-08T10:22:40Z",
      "duration_ms": 7000
    },
    "code": {
      "status": "completed",
      "attempts": 2,
      "started_at": "2026-03-08T10:22:40Z",
      "completed_at": "2026-03-08T10:23:10Z",
      "duration_ms": 30000
    },
    "test": {
      "status": "failed",
      "attempts": 2,
      "started_at": "2026-03-08T10:23:10Z",
      "completed_at": "2026-03-08T10:23:48Z",
      "duration_ms": 38000
    },
    "review": {
      "status": "completed",
      "attempts": 2,
      "started_at": "2026-03-08T10:23:48Z",
      "completed_at": "2026-03-08T10:24:01Z",
      "duration_ms": 13000
    },
    "create_pr": {
      "status": "not_run",
      "attempts": 0,
      "duration_ms": 0
    }
  }
}
```

## Field Definitions

### Identity

- `schema_version`: required string. Start with `execution-metrics/v1`.
- `run_id`: required string. Unique per workflow run. Suggested format: UTC timestamp plus short random suffix.

### Issue

- `issue.mode`: `analysis_only` or `change_request`.
- `issue.analysis_only`: explicit boolean for easy aggregation.
- `issue.source`: one of `cli`, `webhook`, `test_harness`, `unknown`.
- `issue.description`: raw issue text or identifier.

### Workflow

- `workflow.status`: one of `approved`, `failed`, `changes_required`, `aborted`.
- `workflow.started_at`: first observed workflow timestamp.
- `workflow.completed_at`: last observed workflow timestamp.
- `workflow.duration_ms`: end minus start.
- `workflow.retry_count`: final retry count from state.
- `workflow.attempt_count`: `retry_count + 1`.
- `workflow.terminal_node`: last node that produced a terminal decision.
- `workflow.created_pr`: boolean derived from `created_pr_url` presence.

### Workspace

- `workspace.path`: workspace root used by the run.
- `workspace.has_python`: from workspace profile.
- `workspace.has_package_json`: from workspace profile.
- `workspace.frameworks`: normalized framework names.
- `workspace.package_manager`: `npm`, `pnpm`, `yarn`, or `none`.

### Planning

- `planning.retrieval_strategy`: from `planning_context.retrieval_strategy`.
- `planning.candidate_file_count`: count of `planning_context.candidate_scores` when present.
- `planning.graph_seed_file_count`: count of `planning_context.graph_seed_files`.
- `planning.blocked_file_count`: count of `planning_context.blocked_files_to_edit`.
- `planning.files_to_edit_count`: count of final `files_to_edit`.
- `planning.edit_intent_count`: count of structured remediation-aware edit targets carried into coding.

### Coding

- `coding.generated_by`: from `codegen_summary.generated_by`.
- `coding.requested_operations`: from `codegen_summary.requested_operations`.
- `coding.applied_operations`: from `codegen_summary.applied_operations`.
- `coding.failed_operation_count`: length of `codegen_summary.failed_operations`.
- `coding.blocked_operation_count`: length of `codegen_summary.blocked_operations`.
- `coding.patch_count`: length of `patches`.
- `coding.changed_file_count`: distinct patch file count.
- `coding.remediation_applied`: whether coder executed with remediation context on this run.
- `coding.remediation_focus_count`: number of remediation focus areas passed into coding.

### Testing

- `testing.status`: `passed`, `failed`, `not_run`.
- `testing.command_count`: parsed from `test_results` labels or future explicit tester metrics.
- `testing.failed_command_count`: count of non-zero exit labels.
- `testing.failed_commands`: labels whose exit code is non-zero.
- `testing.lint_issue_count`: parsed from `lint:` section when practical, else optional.
- `testing.total_duration_ms`: summed duration across validation commands.
- `testing.sandbox_requested_mode`: configured backend preference such as `auto`, `docker`, `local`, or `docker_required`.
- `testing.sandbox_mode`: resolved backend actually used for this run.
- `testing.sandbox_started`: whether the selected backend started successfully.
- `testing.sandbox_fallback_reason`: fallback or failure reason when the requested backend could not be used directly.
- `testing.validation_strategy`: `full` or `targeted_retry`.
- `testing.retry_policy_reason`: reason the tester chose the current validation strategy.
- `testing.retry_policy_history_source`: whether the decision came from failure-category history, overall history, or a non-history rule.
- `testing.retry_policy_confidence`: confidence level such as `strong`, `weak`, or `limited` for the chosen strategy.
- `testing.retry_policy_stop_reason`: reason the tester marked the current retry attempt as the last useful one if another failure occurs.
- `testing.selected_command_count`: count of commands kept for the current validation pass.
- `testing.skipped_command_count`: count of commands omitted on a targeted retry pass.
- `testing.requested_retry_labels`: labels requested by remediation-aware retry selection.
- `testing.command_reduction_rate`: skipped commands divided by selected plus skipped commands.
- `testing.slowest_command`: label and timing for the slowest validation command.
- `testing.commands`: per-command summary entries with label, exit code, duration, backend mode, and timeout flag.
- `testing.visual_review`: nested summary only when frontend visual review is enabled.

### Testing.VisualReview

- `enabled`: copied from `visual_review.enabled`.
- `screenshot_status`: one of `passed`, `failed`, `missing_artifacts`, `not_configured`.
- `artifact_count`: from `visual_review.artifact_count`.
- `missing_state_count`: number of false required state flags.
- `missing_responsive_category_count`: count of `visual_review.responsive_review.missing_categories`.

### Review

- `review.status`: from `review_summary.status`.
- `review.approved`: final boolean from state.
- `review.comment_count`: length of `review_comments`.
- `review.residual_risk_count`: length of `review_summary.residual_risks`.
- `review.changed_area_count`: length of `review_summary.changed_areas`.
- `review.validation_failed_count`: length of `review_summary.validation.failed`.
- `review.remediation_required`: whether reviewer requested another remediation-guided coding loop.

### Effectiveness

- `effectiveness.retry_attempted`: true when the run reached a retry loop.
- `effectiveness.retry_recovered`: true when a retried run eventually finished approved.
- `effectiveness.remediation_applied`: true when coder consumed remediation context.
- `effectiveness.remediation_recovered`: true when remediation-backed coding ended approved.
- `effectiveness.edit_intent_used`: true when planner emitted focused `edit_intent` guidance.
- `effectiveness.edit_intent_recovered`: true when an `edit_intent`-guided run ended approved.
- `effectiveness.targeted_retry_used`: true when tester selected `targeted_retry`.
- `effectiveness.command_reduction_count`: number of skipped commands on the selected retry pass.
- `effectiveness.command_reduction_rate`: skipped-command ratio for the selected retry pass.

### Failures

- `failures.has_failure`: true when workflow is not approved.
- `failures.primary_category`: one of `validation`, `review`, `policy`, `generation`, `sandbox`, `configuration`, `unknown`.
- `failures.subcategory`: stable operator-facing detail such as `command:script:test`, `blocked_edit_target`, or `docker_unavailable`.
- `failures.taxonomy`: compact object containing both `category` and `subcategory` for dashboard consumers.
- `failures.categories`: deduplicated list of present failure categories.
- `failures.error_message`: top-level `error_message` if present, else first blocking review/test signal.
- `failures.blocking_comment_count`: count of comments that indicate blockers.

### Cross-Run Diagnostics Trend

- `primary_failure_subcategories`: counts for the dominant failure detail across recent runs.
- `failure_subcategory_breakdown`: subcategory to top primary categories summary for dashboard grouping.
- `retry_policy_stop_reasons`: counts of history-driven retry stop reasons across the comparison window.
- `sandbox_fallback_reasons`: counts of sandbox fallback reasons across the comparison window.
- `dashboard`: compact operator summary containing latest and dominant failure category/subcategory plus retry-stop and sandbox-fallback rates.

### Phases

Each phase key is one of `plan`, `code`, `test`, `review`, `create_pr` and stores:

- `status`: `completed`, `failed`, `not_run`, or `unknown`.
- `attempts`: number of times the node executed.
- `started_at`: first timestamp for that phase.
- `completed_at`: last timestamp for that phase.
- `duration_ms`: best-effort elapsed time. In v1 this is derived from event ordering unless explicit node timings are added.

## Normalized Event Schema

The current `execution_events` shape is already useful, but a more explicit event schema should be the next emitter target.

```json
{
  "schema_version": "execution-event/v2",
  "run_id": "20260308T102233Z-8d6f2b8f",
  "sequence": 4,
  "timestamp": "2026-03-08T10:23:10Z",
  "node": "test",
  "event_type": "node_completed",
  "attempt": 2,
  "status": "failed",
  "duration_ms": 38000,
  "details": {
    "test_passed": false,
    "failed_command_count": 1,
    "failed_commands": ["script:build"]
  }
}
```

Recommended additions over the current event payload:

1. `run_id`
2. `sequence`
3. `event_type`
4. `attempt`
5. `duration_ms`
6. normalized `status` values per node

Current implementation status:

1. `run_id`, `sequence`, `event_type`, `attempt`, and `duration_ms` are now emitted on workflow events.
2. `test` events now emit `passed` or `failed` status.
3. `review` events now emit `approved` or `changes_required` status.
4. `node_started` events are now emitted before each node executes.
5. `node_completed` durations are now measured against the matching `node_started` event for the same node attempt.
6. planner/tester/reviewer event details now include retry-loop metadata such as `edit_intent_count`, validation strategy selection, skipped-command counts, and retry recovery flags.

## Derivation Rules From Current State

The first implementation should avoid invasive agent rewrites.

### Available Now

- `execution_events`: node ordering and timestamps
- `retry_count`: workflow retry count
- `files_to_edit`, `patches`: change scope and output volume
- `planning_context`: retrieval and file-policy decisions
- `codegen_summary`: operation counts and policy blocks
- `test_results`: command labels and exit codes
- `test_passed`: top-level test outcome
- `visual_review`: artifact and responsive-review coverage
- `review_comments`, `review_summary`, `review_approved`: review outcome and residual risk summary
- `testing_summary`: validation strategy, command selection, and duration metadata
- `codegen_summary`: remediation usage and blocked/failed operation counts

### Derive In v1

- phase attempts from repeated node names in `execution_events`
- workflow timing from first and last event timestamps
- phase timing from first and last occurrence per node
- testing command counts from `test_results`
- failure categories from `test_passed`, `review_summary`, `error_message`, and policy block counts
- retry effectiveness and command reduction from `retry_count`, `planning_context.edit_intent`, `codegen_summary`, and `testing_summary`

### Add Later When Needed

- LLM token counts and cost by role
- sandbox backend latency and container startup time
- git timings and PR creation metadata
- per-command execution durations from tester
- explicit planner candidate counts emitted directly instead of inferred
- cross-run strategy comparison summaries for operator dashboards

## Failure Taxonomy

Use a small, stable taxonomy so dashboards remain readable.

- `validation`: test command failed or `test_passed == false`
- `review`: reviewer rejected changes or blocking review summary remained
- `policy`: blocked edit operations or blocked target files drove the failure
- `generation`: coder produced no usable patches or operation application failed
- `sandbox`: sandbox command execution infrastructure failed
- `configuration`: missing runtime, package manager, provider, or workspace prerequisites
- `unknown`: fallback when none of the above fit

Recommended subcategories should stay compact and reusable:

- `configuration`: `missing_credentials`, `unsupported_validation_mode`, `configuration_error`
- `sandbox`: backend fallback or runtime details such as `docker_unavailable`, `command_timeout`, `sandbox_runtime_failure`
- `policy`: `blocked_edit_target`
- `validation`: command-scoped labels such as `command:script:test` plus visual-review variants such as `visual_review_missing_states`
- `generation`: `failed_operation`, `no_code_changes`, `generation_failure`
- `review`: `review_changes_required`, `review_blocked`
- `unknown`: `unknown_failure`

`primary_category` should pick the first category in this precedence order:

1. `configuration`
2. `sandbox`
3. `policy`
4. `validation`
5. `generation`
6. `review`
7. `unknown`

## Rollout Plan

### Phase 1: Derived Metrics Only

1. Add `execution_metrics` to `AgentState`.
2. Build it once after review or at workflow completion from the existing state.
3. Surface a compact summary in the CLI JSON output.

### Phase 2: Explicit Node Metrics

1. Emit start and end events per node.
2. Add attempt number and duration to `execution_events`.
3. Stop inferring phase timing from adjacency when explicit timing is available.

### Phase 3: Operator Diagnostics

1. Write metrics JSON as an artifact in `.ai-code-agent/runs/<run_id>/metrics.json`.
2. Add `ai_code_agent.main diagnose` or `runs --latest` style summaries.
3. Feed failure taxonomy into CI dashboards and retry tuning.
4. Surface dashboard-oriented summaries for latest failure detail, dominant recent failure patterns, retry stop rates, and sandbox fallback rates.

## Compatibility Rules

1. Never remove fields from `execution-metrics/v1`; only add optional fields.
2. Keep raw `execution_events` for forensic debugging even after aggregate metrics exist.
3. If a field cannot be derived reliably, prefer `null` over misleading guesses.
