---
name: release-readiness
version: 0.1.0
title: Release Readiness
description: Guide planning for versioning, validation gates, runtime support, and release checklist work.
tags: release, readiness, version, changelog, validation, ci, runtime, support-matrix, checklist
triggers: 1.0 checklist, release readiness, version bump, validation gate, supported runtime, support matrix, changelog, release notes
frameworks: python
permission: read-only
sandbox: optional
input_schema: {"type": "object", "properties": {"issue": {"type": "string"}, "workspace_profile": {"type": "object"}, "runtime_matrix": {"type": "string"}}, "required": ["issue", "workspace_profile"]}
output_schema: {"type": "object", "properties": {"release_checks": {"type": "array"}, "validation_gates": {"type": "array"}}, "required": ["release_checks"]}
---

Use this skill when the task is about shipping quality rather than feature implementation.

Planning should explicitly cover:

- version consistency and release metadata
- validation gates in local and CI flows
- documented support scope and runtime matrix accuracy
- rollback or residual-risk considerations for the release

Prefer checklist-shaped plans and evidence from real repo files over assumptions.