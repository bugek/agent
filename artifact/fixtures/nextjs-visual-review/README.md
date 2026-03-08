# Next.js Visual Review Fixture

This fixture is a minimal Next.js example repo that already speaks the AI Code Agent visual-review artifact contract.

## Scripts

- `npm run dev`: starts the sample app on `http://127.0.0.1:3000`
- `npm run visual-review`: starts the app, captures desktop and mobile Playwright screenshots, and writes artifact metadata

## Contract

The `visual-review` script reads these env vars when they are present:

- `AI_CODE_AGENT_VISUAL_REVIEW_DIR`
- `AI_CODE_AGENT_VISUAL_REVIEW_MANIFEST`
- `AI_CODE_AGENT_PLAYWRIGHT_SCREENSHOT_DIR`

When run by `TesterAgent`, those env vars are injected automatically. The script writes:

- screenshots under `.ai-code-agent/visual-review/screenshots/`
- a manifest at `.ai-code-agent/visual-review/manifest.json`
- viewport metadata that lets the reviewer verify mobile and desktop coverage

## Local Usage

1. `npm install`
2. `npx playwright install chromium`
3. `npm run visual-review`

## Sample Manifest

```json
{
  "tool": "playwright",
  "generated_at": "2026-03-08T00:00:00.000Z",
  "base_url": "http://127.0.0.1:3000",
  "artifacts": [
    {
      "kind": "screenshot",
      "path": "screenshots/home-desktop.png",
      "route": "/",
      "title": "Visual Review Fixture",
      "device": "desktop",
      "viewport": {
        "width": 1440,
        "height": 960
      }
    },
    {
      "kind": "screenshot",
      "path": "screenshots/home-mobile.png",
      "route": "/",
      "title": "Visual Review Fixture",
      "device": "mobile",
      "viewport": {
        "width": 393,
        "height": 852
      }
    }
  ]
}
```

## Notes

- `PLAYWRIGHT_BASE_URL` can target an already-running app instead of spawning `npm run dev`.
- `PLAYWRIGHT_WEB_SERVER_COMMAND` can override the startup command.
- This fixture is intentionally small; its purpose is to demonstrate the tester/reviewer artifact contract, including responsive coverage, not app complexity.