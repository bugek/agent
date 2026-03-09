# Product Roadmap

This document captures the working product roadmap for AI Code Agent after the `v0.9.0` baseline. It is meant to be used as an execution guide for planning, prioritization, and release tracking.

## Current Baseline

- Current declared version: `v0.9.0`
- Current strengths:
  - End-to-end planner, coder, tester, reviewer flow is working.
  - Execution metrics and diagnostics are persisted.
  - Next.js and NestJS validation paths exist.
  - Visual review and monitor flow are now working with Docker-backed screenshot capture.
- Current gap to `v1.0.0`:
  - Product scope is not frozen yet.
  - Release criteria are not yet formalized.
  - Multi-repo, skill extensibility, and richer sandbox orchestration are still future-facing.

## Roadmap Principles

1. Prioritize capabilities that improve real issue-run success rate and operator visibility.
2. Expand blast radius gradually: single-repo first, then multi-service, then multi-repo.
3. Every new capability must show up in metrics, diagnostics, and the monitor.
4. Prefer stable abstractions over one-off feature branches in prompts or special cases.
5. Do not call something `v1.0.0` until support scope, release criteria, and operational guidance are explicit.

## Version Roadmap

## v0.10

Goal: make the agent more extensible and able to operate against service-backed repositories while keeping the system observable.

Primary themes:

- Skills and structured capability extension.
- Docker Compose sandbox support.
- Better monitor visibility into skills and sandbox execution.

Planned issues:

### 1. Skill Manifest And Registry

Summary:

Create a formal registry for reusable agent skills so capability growth is driven by manifests instead of hardcoded prompt behavior.

Acceptance criteria:

- A skill manifest format exists with name, version, description, input schema, output schema, and permission level.
- The runtime can load registered skills from one registry location.
- Invalid manifests fail with clear validation errors.
- Unit tests cover valid and invalid manifest loading.

### 2. Planner Skill Selection

Summary:

Allow the planner to select one or more skills from issue intent and workspace profile.

Acceptance criteria:

- Planner output records selected skills and why they were chosen.
- Runs without a matching skill fall back to the standard planning path.
- Multiple candidate skills are ranked deterministically.
- Tests cover match, no-match, and multi-match cases.

### 3. Skill Execution Telemetry

Summary:

Expose skill usage through execution metrics, diagnostics, and monitor payloads.

Acceptance criteria:

- Metrics record invoked skill names, phase, and outcome.
- Monitor payload exposes skill invocation summaries.
- Failed skill execution produces explicit failure telemetry.
- Tests cover serialization and monitor rendering inputs.

### 4. Docker Compose Sandbox Backend

Summary:

Add a compose-backed sandbox mode for repositories that need multiple services.

Acceptance criteria:

- Sandbox runner supports a compose mode in addition to local and single-container Docker.
- A run can start, execute commands against, and stop a compose stack.
- Compose resources are cleaned up at the end of the run.
- Regression tests cover startup, command execution, and cleanup.

### 5. Compose Service Readiness And Logs

Summary:

Make compose-backed runs debuggable by waiting for service readiness and preserving service logs.

Acceptance criteria:

- Validation does not start until required services are ready.
- Service readiness failures identify the blocking service.
- Service logs are captured into run artifacts.
- Diagnostics or monitor output exposes log locations or summaries.

### 6. Monitor Skill And Sandbox View

Summary:

Extend the monitor so operators can see which skills ran and which sandbox backend was used.

Acceptance criteria:

- The monitor shows the sandbox backend used by the run.
- The monitor shows invoked skills and outcomes.
- The selected-step view includes skill and sandbox context where applicable.
- Frontend and backend tests cover the new payload and rendering behavior.

Exit criteria for `v0.10`:

- Skills exist as a first-class extensibility surface.
- Compose-based sandbox execution works for at least one committed fixture.
- Monitor and diagnostics expose both skills and sandbox backend context.

## v0.11

Goal: extend the system from strong single-repo execution into controlled cross-repo orchestration.

Primary themes:

- Multi-repo awareness.
- Cross-repo retrieval and planning.
- Multi-PR publishing and repo-scoped observability.

Planned issues:

### 7. Multi-Repo Workspace Model

Summary:

Introduce a workspace model that can represent multiple repositories inside one run.

Acceptance criteria:

- State and configuration can represent more than one repo target.
- Run artifacts record repo scope for operations.
- Tests cover at least one two-repo fixture.

### 8. Repo-Aware Planning

Summary:

Teach the planner to decide which repo each proposed change belongs to.

Acceptance criteria:

- Planning output maps edit intent and files to repo targets.
- Retrieval reasoning can explain why a repo was selected.
- Single-repo issues do not expand into multi-repo plans unnecessarily.

### 9. Cross-Repo Retrieval Graph

Summary:

Extend retrieval to traverse dependency and symbol relationships across repositories.

Acceptance criteria:

- Retrieval graph expansion can cross repo boundaries.
- Candidate explanations identify the repo-to-repo link that caused selection.
- Fixture-based tests show cross-repo candidate discovery working.

### 10. Repo-Scoped Patch Routing

Summary:

Ensure code generation and patch application are routed to the correct repository.

Acceptance criteria:

- Patches are applied to the correct repo root.
- Changed file summaries include repo identity.
- Incorrect repo routing is blocked or fails loudly.
- Reviewer summaries can show changed areas per repo.

### 11. Multi-PR Publishing

Summary:

Allow one run to create or reuse multiple pull requests when changes span multiple repositories.

Acceptance criteria:

- Publish step supports multiple PR outcomes in a single run.
- Metrics record PR outcomes per repo.
- Partial publish success is represented clearly.
- Provider integration tests cover mocked multi-PR behavior.

### 12. Approval Gates For Multi-Repo Changes

Summary:

Add stricter gates before cross-repo changes can be published.

Acceptance criteria:

- Multi-repo runs can be blocked by policy before publish.
- Block reasons are visible in telemetry and diagnostics.
- Tests cover allowed and denied multi-repo publish paths.

### 13. Monitor Repo-Lane View

Summary:

Make the monitor understandable for multi-repo runs.

Acceptance criteria:

- Monitor can display repo-specific execution context.
- Operators can see which phases touched which repos.
- Single-repo runs remain simple and readable.

Exit criteria for `v0.11`:

- Multi-repo planning and execution work in at least one committed scenario.
- Publish step can represent multi-PR outcomes.
- Monitor and diagnostics expose repo-scoped execution clearly.

## v1.0+

Goal: harden the product into a stable platform with enforceable policy, public contracts, and operational guidance.

Primary themes:

- Safety and policy.
- Stable contracts.
- Platform and operator maturity.

Planned issues:

### 14. Skill Permission Model

Summary:

Introduce permissions for skill execution so the system can distinguish read-only, codegen, sandbox, and publish behaviors.

Acceptance criteria:

- Skills declare a permission class.
- The orchestrator enforces permission checks before execution.
- Blocked skill invocations are recorded in telemetry.
- Tests cover allowed and denied paths.

### 15. Sandbox Backend Abstraction

Summary:

Unify local, Docker, and compose execution under one stable backend abstraction.

Acceptance criteria:

- Sandbox backends share one clear runtime contract.
- Business logic does not depend on backend-specific branching beyond the abstraction boundary.
- Contract tests cover all supported backends.

### 16. Policy Engine For Repo, Skill, And Sandbox

Summary:

Create a policy layer that controls repo scope, skill usage, and sandbox mode.

Acceptance criteria:

- Policy config can allow or deny repo targets, skill classes, and sandbox backends.
- Policy violations are surfaced in metrics, execution events, and diagnostics.
- Default policy is safe for team usage.

### 17. Stable Public Contracts

Summary:

Freeze the public-facing contracts for CLI, metrics, and monitor payloads.

Acceptance criteria:

- Stable CLI surface is documented.
- Stable metrics fields are documented.
- Stable monitor payload fields are documented.
- A deprecation and breaking-change policy exists.

### 18. Run Comparison And Diagnostics Dashboard

Summary:

Improve diagnostics so operators can compare runs and understand regressions or recoveries quickly.

Acceptance criteria:

- Runs can be compared across retries or across recent history.
- Diagnostics can summarize regressions and recoveries.
- Operators can inspect run deltas without reading raw logs first.

### 19. Operator Onboarding And Runbooks

Summary:

Add the user and operator documentation needed for a release-grade product.

Acceptance criteria:

- Quickstart exists for a new user.
- Troubleshooting exists for provider auth, monitor, sandbox, and visual review.
- Operator runbook exists for the most common failure categories.

### 20. Release Governance For 1.x

Summary:

Formalize release discipline for stable releases.

Acceptance criteria:

- Changelog exists.
- Release checklist exists.
- Support matrix exists.
- Breaking-change policy exists.
- Release gates are documented and enforced.

Exit criteria for `v1.0.0`:

- Support scope is declared explicitly.
- Release criteria are formalized.
- Public contracts are defined.
- Operator docs are present.
- Validation and sandbox behavior are stable enough to support the declared scope.

## Suggested Execution Order

Recommended order across the backlog:

1. Skill Manifest And Registry
2. Planner Skill Selection
3. Skill Execution Telemetry
4. Docker Compose Sandbox Backend
5. Compose Service Readiness And Logs
6. Monitor Skill And Sandbox View
7. Multi-Repo Workspace Model
8. Repo-Aware Planning
9. Cross-Repo Retrieval Graph
10. Repo-Scoped Patch Routing
11. Multi-PR Publishing
12. Approval Gates For Multi-Repo Changes
13. Monitor Repo-Lane View
14. Skill Permission Model
15. Sandbox Backend Abstraction
16. Policy Engine For Repo, Skill, And Sandbox
17. Stable Public Contracts
18. Run Comparison And Diagnostics Dashboard
19. Operator Onboarding And Runbooks
20. Release Governance For 1.x

## Notes

- `v0.10` should optimize for extensibility and better repository realism.
- `v0.11` should optimize for larger change scope without losing control.
- `v1.0+` should optimize for policy, stability, and operator trust.
- New features should not be considered done unless they are visible in metrics and diagnosable in monitor or diagnostics output.