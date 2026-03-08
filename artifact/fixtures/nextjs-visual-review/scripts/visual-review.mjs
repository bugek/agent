import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { chromium } from "@playwright/test";

const workspaceDir = process.cwd();
const artifactRoot = process.env.AI_CODE_AGENT_VISUAL_REVIEW_DIR || path.join(workspaceDir, ".ai-code-agent", "visual-review");
const manifestPath = process.env.AI_CODE_AGENT_VISUAL_REVIEW_MANIFEST || path.join(artifactRoot, "manifest.json");
const screenshotDir = process.env.AI_CODE_AGENT_PLAYWRIGHT_SCREENSHOT_DIR || path.join(artifactRoot, "screenshots");
const baseUrl = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";
const command = process.env.PLAYWRIGHT_WEB_SERVER_COMMAND || "npm run dev";
const startupTimeoutMs = Number(process.env.PLAYWRIGHT_WEB_SERVER_TIMEOUT_MS || "45000");

const routes = [
  {
    route: "/",
    captures: [
      {
        title: "Home Desktop",
        fileName: "home-desktop.png",
        viewport: { width: 1440, height: 960 },
        device: "desktop",
      },
      {
        title: "Home Mobile",
        fileName: "home-mobile.png",
        viewport: { width: 393, height: 852 },
        device: "mobile",
      },
    ],
  },
];

async function main() {
  await rm(artifactRoot, { recursive: true, force: true });
  await mkdir(screenshotDir, { recursive: true });

  const server = spawnServer(command);
  try {
    await waitForServer(baseUrl, startupTimeoutMs);
    const browser = await chromium.launch({ headless: true });
    const artifacts = [];

    for (const entry of routes) {
      for (const capture of entry.captures) {
        const page = await browser.newPage({ viewport: capture.viewport });
        await page.goto(new URL(entry.route, baseUrl).toString(), { waitUntil: "networkidle" });
        const outputPath = path.join(screenshotDir, capture.fileName);
        await page.screenshot({ path: outputPath, fullPage: true });

        artifacts.push({
          kind: "screenshot",
          path: path.relative(artifactRoot, outputPath).split(path.sep).join("/"),
          route: entry.route,
          title: await page.title(),
          status: page.url(),
          viewport: capture.viewport,
          device: capture.device,
        });
        await page.close();
      }
    }

    await browser.close();

    await writeManifest({
      tool: "playwright",
      generated_at: new Date().toISOString(),
      base_url: baseUrl,
      artifacts,
    });
  } finally {
    await stopServer(server);
  }
}

function spawnServer(rawCommand) {
  return spawn(rawCommand, {
    cwd: workspaceDir,
    stdio: "inherit",
    shell: true,
    env: { ...process.env },
  });
}

async function stopServer(server) {
  if (server.exitCode !== null || server.killed) {
    return;
  }

  await new Promise((resolve) => {
    const finish = () => resolve();
    server.once("exit", finish);
    server.once("close", finish);

    if (process.platform === "win32" && server.pid) {
      const killer = spawn("taskkill", ["/pid", String(server.pid), "/T", "/F"], {
        stdio: "ignore",
      });
      killer.once("exit", () => undefined);
      killer.once("close", () => undefined);
    } else {
      server.kill("SIGTERM");
    }

    setTimeout(() => {
      if (server.exitCode === null) {
        if (process.platform === "win32" && server.pid) {
          spawn("taskkill", ["/pid", String(server.pid), "/T", "/F"], { stdio: "ignore" });
        } else {
          server.kill("SIGKILL");
        }
      }
    }, 5000).unref();
  });
}

async function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // Server not ready yet.
    }
    await sleep(1000);
  }

  throw new Error(`Timed out waiting for ${url}`);
}

async function writeManifest(payload) {
  await mkdir(path.dirname(manifestPath), { recursive: true });
  await writeFile(manifestPath, JSON.stringify(payload, null, 2), "utf-8");
  const written = JSON.parse(await readFile(manifestPath, "utf-8"));
  console.log(`Wrote visual review manifest with ${written.artifacts.length} artifact(s) to ${manifestPath}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});