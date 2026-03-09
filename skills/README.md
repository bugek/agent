# Local Skills

This repository uses a local, Agent Skills-compatible packaging model.

Each skill lives in its own folder under `skills/` and must include `SKILL.md`.

Supported metadata today lives in YAML-style frontmatter at the top of `SKILL.md`:

- `name`
- `title`
- `description`
- `tags`
- `triggers`
- `frameworks`
- `permission`
- `sandbox`

The remainder of `SKILL.md` is treated as the skill instructions body.

This first implementation only loads and selects skills during planning. It does not yet execute skill-specific scripts or enforce skill-scoped permissions.