---
name: frontend-visual-review
version: 0.1.0
title: Frontend Visual Review
description: Guide planning for UI work that should preserve screenshots, responsive coverage, and explicit loading or error states.
tags: frontend, ui, page, layout, component, screenshots, monitor, visual-review, playwright
triggers: visual review, screenshot, screenshots, monitor ui, loading state, empty state, error state, responsive, layout, page, component
frameworks: nextjs, react, vite
permission: read-only
sandbox: optional
input_schema: {"type": "object", "properties": {"issue": {"type": "string"}, "workspace_profile": {"type": "object"}}, "required": ["issue", "workspace_profile"]}
output_schema: {"type": "object", "properties": {"planning_notes": {"type": "array"}, "coverage_focus": {"type": "array"}}, "required": ["planning_notes"]}
---

Use this skill when the issue is about frontend behavior, visual polish, or screenshot-backed review.

Planning should explicitly call out:

- the route or component surface being changed
- loading, empty, error, and success states when they apply
- responsive coverage expectations for desktop and mobile
- whether existing visual-review artifacts or monitor screenshots should stay stable

Prefer edits that preserve the current screenshot and monitor contract unless the issue requires a deliberate UI change.