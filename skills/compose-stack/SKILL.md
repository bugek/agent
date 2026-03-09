---
name: compose-stack
version: 0.1.0
title: Compose Stack
description: Guide planning for multi-service repositories that need Docker Compose orchestration or dependent services.
tags: compose, docker compose, service, postgres, redis, integration, multi-service
triggers: compose sandbox, docker compose, service readiness, postgres, redis, integration stack
frameworks: python, nextjs, nestjs
permission: sandbox
sandbox: required
input_schema: {"type": "object", "properties": {"issue": {"type": "string"}, "workspace_profile": {"type": "object"}, "compose_file": {"type": "string"}}, "required": ["issue", "workspace_profile"]}
output_schema: {"type": "object", "properties": {"required_services": {"type": "array"}, "artifact_expectations": {"type": "array"}}, "required": ["required_services"]}
---

Use this skill when the task requires multiple dependent services, not just a single-process workspace.

Planning should explicitly call out:

- the compose file or service topology being used
- which services must be ready before validation starts
- what logs or artifacts should be captured if startup fails
- whether cleanup must stop and remove the stack after validation