# AI Code Agent Version Feature List

## Versioning Strategy

ใช้ semantic versioning แบบ practical:

1. `0.x` สำหรับช่วง product incubation
2. `1.0` เมื่อรองรับ production-grade single-repo workflows ได้อย่างน่าเชื่อถือ
3. minor release สำหรับ capability ใหม่
4. patch release สำหรับ stability, fixes, and provider updates

## v0.1.0 Foundation MVP

เป้าหมาย: ให้ระบบรันได้จริงแบบ local-first

Features:
1. Python package scaffold
2. CLI orchestration flow
3. Provider abstraction สำหรับ Anthropic, OpenAI, OpenRouter
4. Fallback mode เมื่อไม่มี API key
5. Basic planner, coder, tester, reviewer agents
6. Docker/local sandbox execution
7. Health check command
8. Role-based model selection

Exit Criteria:
1. รัน workflow ได้ end-to-end
2. รัน smoke tests ได้
3. import และ compile ผ่าน

## v0.2.0 Reliable Editing Core

เป้าหมาย: เพิ่มความน่าเชื่อถือของการแก้โค้ดจริง

Features:
1. Multi-file create/update/delete operations
2. Safer patch schema
3. Better planner file targeting
4. Structured execution trace
5. Retry policy refinement
6. Failure reason classification

Exit Criteria:
1. แก้หลายไฟล์ได้ใน task เดียว
2. ลด no-op patches
3. review feedback มีโครงสร้างชัดเจนขึ้น

## v0.3.0 JavaScript/TypeScript Runtime Support

เป้าหมาย: รองรับ repo ที่ไม่ใช่ Python อย่างจริงจัง

Features:
1. Detect npm, pnpm, yarn
2. Detect package.json scripts
3. Run lint/build/test for JS/TS repos
4. Improve Docker image for Node-based projects
5. Better workspace scanning for frontend/backend repos

Exit Criteria:
1. รัน validation กับ JS/TS repos ได้
2. รู้จักคำสั่ง build/test จาก repo เอง

## v0.4.0 Next.js Support

เป้าหมาย: รองรับ modern React app workflows

Features:
1. Detect Next.js app/pages router
2. Understand route, layout, page, component structure
3. Next-aware planner prompts
4. Run `build`, `lint`, and optional typecheck
5. Support creating/updating Next.js pages, layouts, UI components, and API routes
6. Basic frontend design brief support
7. Distinguish route files, layout files, special app router files, and API routes in planning context

Exit Criteria:
1. แก้ feature หรือ bug ใน Next.js app ได้อย่างน่าเชื่อถือ
2. สร้าง page/component ใหม่ได้

## v0.5.0 NestJS Support

เป้าหมาย: รองรับ backend service architecture แบบนิยมใช้

Features:
1. Detect NestJS modules, controllers, services, DTOs
2. Nest-aware planner prompts
3. Support backend file scaffolding and app module wiring
4. Run build, lint, typecheck, and test commands
5. Understand API flow across module boundaries
6. Target feature directories from route, resource, and endpoint language in issues

Exit Criteria:
1. แก้ controller/service/module flows ได้
2. เพิ่ม endpoint หรือ service logic ใหม่ได้
3. register feature modules into the root application module when needed

## v0.6.0 Hybrid Retrieval

เป้าหมาย: เพิ่มความแม่นของ context selection

Features:
1. File classification index
2. Symbol and import graph
3. Semantic code retrieval
4. Ranking pipeline for planner context
5. Pattern reuse from repository history

Exit Criteria:
1. retrieval precision ดีขึ้นอย่างวัดผลได้
2. planner เลือก target files ได้แม่นขึ้นใน repo ใหญ่

## v0.7.0 Frontend Quality Layer

เป้าหมาย: ยกระดับจาก “generate ได้” เป็น “หน้าตาดีและใช้ได้จริง”

Current progress:
1. Substantially implemented
2. Deterministic Next.js scaffolding now emits stronger visual direction instead of plain placeholder sections.
3. Generated Next.js components now cover `loading`, `empty`, `error`, and `ready` states.
4. App Router page scaffolding now emits companion `loading.tsx` and `error.tsx` files when applicable.
5. Unit tests cover frontend-quality templates directly.
6. Tester and reviewer now validate responsive viewport coverage from visual-review artifacts, with committed desktop and mobile fixture captures.
7. Screenshot-based review loop is wired into the full validation suite and CI through the committed Next.js visual-review smoke fixture.

Features:
1. UI design direction input
2. Design token generation
3. Better prompts for layout, typography, color, motion
4. State coverage: loading, empty, error, success
5. Responsive review checks
6. Optional screenshot-based review loop
7. Committed Next.js visual-review fixture with Playwright manifest example

Exit Criteria:
1. application front มี visual direction ที่ชัดขึ้น
2. ลด boilerplate-looking UI

## v0.8.0 Collaboration and Review Controls

เป้าหมาย: ใช้งานในทีมได้ดีขึ้น

Current progress:
1. Complete
2. AgentConfig supports policy-based file restrictions through allow/deny glob rules, and coder enforcement records blocked operations in `codegen_summary`.
3. Planner filters denied `files_to_edit` targets before they reach coding, which reduces accidental edits outside the team-approved scope.
4. Reviewer emits structured review summaries covering changed areas, validation outcomes, visual-review status, residual risks, and remediation guidance.
5. Planner, coder, and tester now coordinate retry loops through remediation-aware `edit_intent` plus `targeted_retry` validation selection.
6. `execution_events` retain planner, coder, tester, and reviewer decision metadata so runs can be audited without replaying the whole workflow.
7. Diagnose output and persisted metrics now summarize retry recovery, remediation effectiveness, edit-intent recovery, and targeted-retry command savings for operators.

Features:
1. Approval gates
2. Policy-based file restrictions
3. Richer review summaries
4. GitHub/ADO issue and PR workflows ที่ลึกขึ้น
5. Audit trail for agent decisions
6. Remediation-aware retry coordination
7. Operator-facing retry effectiveness metrics

Exit Criteria:
1. ทีม review งานจาก agent ได้ง่ายขึ้น
2. ควบคุม risk ของ auto-changes ได้ดีขึ้น
3. retry behavior อธิบายและวัดผลย้อนหลังได้จาก diagnostics

## v0.9.0 Production Readiness

เป้าหมาย: เตรียมขึ้น production workload

Current progress:
1. Released as the current baseline for the production-readiness foundation.
2. Execution metrics schema is now defined in `artifact/execution_metrics_schema.md`, with a run-level aggregate model and a normalized event target layered over existing telemetry.
3. Workflow runs now persist `.ai-code-agent/runs/<run_id>/metrics.json`, and `ai_code_agent.main diagnose` can inspect the latest or requested run artifact.
4. `execution_events` now emit `node_started` and `node_completed` records with run id, sequence, attempt, normalized status, and duration metadata.
5. Tester command execution now records per-command durations, timeout flags, and slowest-command summaries into run-level testing metrics.
6. Retry orchestration now uses recent `execution_metrics` history to choose between `targeted_retry` and `full`, and records policy reason plus history source in testing summaries and audit trails.
7. The orchestrator can now stop retrying after a failed full fallback that followed a targeted retry, reducing unproductive remediation loops.
8. GitHub issue URLs and Azure DevOps work item URLs can now be resolved into issue context before planning, and successful auto-push runs can publish provider-specific PRs plus source-thread comments when credentials are configured.
9. Sandbox backend negotiation now supports `auto`, `docker`, `local`, and `docker_required`, while testing metrics record the resolved backend and fallback reason for operator diagnostics.
10. Retry-policy tuning now records confidence and history-based stop reasons, and can stop another remediation loop when both available strategies show low recovery probability in recent history.

Release notes:
1. Bumps the package baseline to `0.9.0`.
2. Makes persisted metrics artifacts and diagnose output part of the official operator workflow.
3. Ships adaptive retry-policy selection from recent run history as the first production-readiness tuning step.
4. Adds a stop condition after failed full fallback retries so remediation loops can terminate earlier when another pass is unlikely to help.
5. Adds provider-aware issue enrichment and remote PR publishing for GitHub and Azure DevOps workflows.
6. Adds inspectable sandbox backend negotiation with fallback diagnostics for local versus Docker execution.
7. Adds richer retry-policy confidence and low-recovery stop signals for more selective remediation loops.

Features:
1. CI integration
2. Better sandbox backends including remote options
3. Retry orchestration tuning
4. Metrics and observability
5. Error taxonomy and operator dashboard basics

Exit Criteria:
1. ใช้ใน CI หรือ controlled production loop ได้
2. วิเคราะห์ failure patterns ได้จริง

## v1.0.0 Product Baseline

เป้าหมาย: เป็น product baseline ที่ทีมใช้งานประจำได้

Features:
1. Stable multi-stack support
2. Reliable retrieval and validation
3. Framework-aware change planning
4. Reviewable PR output
5. Product docs, onboarding, and operational guidance

Exit Criteria:
1. รองรับ Python + Next.js + NestJS อย่างเสถียร
2. มี workflow ตั้งแต่ issue ถึง validated change ที่คาดการณ์ได้
3. พร้อมขยายไป use cases ขั้นสูงกว่าเดิม