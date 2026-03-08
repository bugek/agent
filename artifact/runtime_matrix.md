# Runtime Matrix

This document defines the runtime versions that matter for local development, committed smoke fixtures, and CI validation.

## Matrix

| Surface | Runtime | Requirement | Why this version | Source of truth |
| --- | --- | --- | --- | --- |
| Main repo CLI and validation entrypoints | Python | 3.11 | The repository and CI validation workflow are exercised with Python 3.11, which is the supported baseline for the current package and test suite. | `.github/workflows/validation.yml`, `pyproject.toml` |
| Sandbox image | Node.js | 22.x | The default Docker sandbox should match the strictest Node runtime used by repository validation so live issue runs do not silently undercut the framework baseline. | `Dockerfile`, `ai_code_agent/config.py` |
| CI validation workflow | Node.js | 22.x | CI uses one modern Node line that satisfies the strictest fixture requirement, avoids split-brain behavior between fixtures, and stays comfortably above Next 16's minimum runtime. | `.github/workflows/validation.yml` |
| NestJS smoke fixture | Node.js | >=20 | Nest 11 support targets modern Node releases; using a Node 20+ floor keeps the fixture aligned with the framework's supported runtime family without over-constraining local runs to a single patch line. | `artifact/fixtures/nestjs-smoke/package.json` |
| Next.js visual-review fixture | Node.js | >=20.9.0 | Next 16 requires Node 20.9.0 or newer. The fixture declares that exact floor because the smoke harness runs real `next dev`, `next build`, and Playwright-driven screenshot capture. | `artifact/fixtures/nextjs-visual-review/package.json`, `artifact/fixtures/nextjs-visual-review/package-lock.json` |
| Playwright in the Next.js fixture | Node.js | inherits fixture runtime | Playwright runs inside the Next fixture workflow, so it inherits the `>=20.9.0` Node floor. The fixture is pinned to `@playwright/test 1.58.2` because that patch line removes the current audit finding present in older 1.52.x releases. | `artifact/fixtures/nextjs-visual-review/package.json` |

## Decision Rules

1. CI can run newer patch or minor versions than a fixture minimum, but it must never run below the highest minimum declared by any committed fixture.
2. Fixture `engines.node` values define compatibility floors; CI picks a single version high enough to satisfy all of them at once.
3. When a framework upgrade raises a runtime minimum, update the fixture manifest first, then update this matrix, then rerun the full validation suite.
4. Security-driven dependency bumps inside fixtures must preserve the declared runtime floor unless the framework itself requires a higher one.

## Current Interpretation

- If you want one local Node version that matches CI behavior, use Node 22.
- The default sandbox image should also run Node 22 so workflow validation matches CI and fixture expectations.
- If you only run the NestJS smoke fixture locally, Node 20+ is sufficient.
- If you run the Next.js visual-review fixture locally, use Node 20.9+ or newer.
- If you run the full repository validation suite locally, Node 22 is the safest default because it matches CI and satisfies both fixtures.

## Validation Modes

- `python -m ai_code_agent.validation --mode quick`: local fast loop for compile plus unit tests only.
- `python -m ai_code_agent.validation --mode full`: full validation path used by CI, including NestJS smoke, Next.js visual-review smoke, and retrieval evaluation.
- `.github/workflows/validation.yml` is expected to call `--mode full` explicitly so CI behavior does not depend on CLI defaults.