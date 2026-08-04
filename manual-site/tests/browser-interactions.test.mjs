import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createServer as createHttpServer } from "node:http";
import { createServer } from "node:net";
import { after, before, test } from "node:test";
import { chromium } from "playwright-core";

const browsers = new Set();
let baseUrl;
let chromiumArgs;
let chromiumPath;
let port;
let readinessToken;
let server;

function findAvailablePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close((error) => {
        if (error) {
          reject(error);
        } else {
          resolve(address.port);
        }
      });
    });
  });
}

function isServerGroupRunning() {
  if (!server || server.pid === undefined) return false;
  if (process.platform === "win32") {
    return server.exitCode === null && server.signalCode === null;
  }
  try {
    process.kill(-server.pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code === "EPERM") return true;
    throw error;
  }
}

async function waitForServerGroupToStop(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!isServerGroupRunning()) return true;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return !isServerGroupRunning();
}

function killServerGroup(signal) {
  if (!server) return;
  try {
    if (process.platform === "win32") {
      server.kill(signal);
    } else {
      process.kill(-server.pid, signal);
    }
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

async function responderHasReadinessToken(url, expectedToken) {
  const response = await fetch(`${url}/api/readiness`, {
    cache: "no-store",
  });
  return response.ok && (await response.text()) === expectedToken;
}

async function stopServer() {
  if (!server) return;
  killServerGroup("SIGTERM");
  if (!(await waitForServerGroupToStop(5_000))) {
    killServerGroup("SIGKILL");
    if (!(await waitForServerGroupToStop(5_000))) {
      throw new Error("Manual site preview process group did not stop");
    }
  }
}

async function waitForServer() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (!isServerGroupRunning()) {
      throw new Error(
        `Manual site development server exited before becoming ready (code ${server.exitCode}, signal ${server.signalCode})`,
      );
    }
    try {
      if (
        (await responderHasReadinessToken(baseUrl, readinessToken)) &&
        isServerGroupRunning()
      ) {
        return;
      }
    } catch {
      // The development server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Manual site development server did not start");
}

before(async () => {
  port = await findAvailablePort();
  baseUrl = `http://127.0.0.1:${port}`;
  readinessToken = randomUUID();
  server = spawn(
    process.platform === "win32" ? "npm.cmd" : "npm",
    [
      "run",
      "start",
      "--",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: new URL("..", import.meta.url),
      env: {
        ...process.env,
        MANUAL_PREVIEW_READINESS_TOKEN: readinessToken,
        NO_COLOR: "1",
      },
      stdio: "ignore",
      detached: process.platform !== "win32",
    },
  );
  await waitForServer();
  const originalTmpdir = process.env.TMPDIR;
  const originalGetuid = process.getuid;
  process.env.TMPDIR = "/tmp";
  // tar-fs tries to restore archive ownership when tests run as root, which is
  // unsupported in some containers. Browser files do not require that step.
  if (originalGetuid?.() === 0) process.getuid = () => 1000;
  const { default: chromiumBinary } = await import("@sparticuz/chromium");
  chromiumPath = await chromiumBinary.executablePath();
  chromiumArgs = chromiumBinary.args;
  if (originalTmpdir === undefined) {
    delete process.env.TMPDIR;
  } else {
    process.env.TMPDIR = originalTmpdir;
  }
  if (originalGetuid) process.getuid = originalGetuid;
});

after(async () => {
  await Promise.all([...browsers].map((browser) => browser.close()));
  await stopServer();
});

async function launchBrowser() {
  const browser = await chromium.launch({
    executablePath: chromiumPath,
    args: chromiumArgs,
    headless: true,
  });
  browsers.add(browser);
  return browser;
}

test("rejects an HTTP responder from a different preview run", async () => {
  const otherPreview = createHttpServer((request, response) => {
    response.writeHead(200, {
      connection: "close",
      "content-type": "text/plain",
    });
    response.end("another-preview-token");
  });
  await new Promise((resolve, reject) => {
    otherPreview.once("error", reject);
    otherPreview.listen(0, "127.0.0.1", resolve);
  });

  try {
    const address = otherPreview.address();
    assert.ok(address && typeof address !== "string");
    assert.equal(
      await responderHasReadinessToken(
        `http://127.0.0.1:${address.port}`,
        readinessToken,
      ),
      false,
    );
  } finally {
    otherPreview.closeAllConnections();
    await new Promise((resolve, reject) => {
      otherPreview.close((error) => (error ? reject(error) : resolve()));
    });
  }
});

test("filters natural multi-word queries and follows result anchors", async () => {
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(baseUrl);

  await page.getByRole("button", { name: "マニュアルを検索" }).click();
  await page.getByRole("searchbox", { name: "検索キーワード" }).fill("参加者 登録");

  const result = page
    .locator(".search-results")
    .getByRole("link", { name: /参加登録/ });
  await assert.doesNotReject(() => result.waitFor());
  assert.equal(
    await page
      .locator(".search-results")
      .getByRole("link", { name: /管理者設定/ })
      .count(),
    0,
  );

  await result.click();
  await page.waitForURL(`${baseUrl}/participant#registration`);
  assert.equal(new URL(page.url()).hash, "#registration");
});

test("opens and closes the navigation menu on a narrow viewport", async () => {
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(baseUrl);

  const menuButton = page.getByRole("button", { name: "メニューを開く" });
  await menuButton.click();
  assert.equal(await menuButton.getAttribute("aria-expanded"), "true");
  await menuButton.click();
  assert.equal(await menuButton.getAttribute("aria-expanded"), "false");
});

test("keeps every search result reachable in a short mobile viewport", async () => {
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 667, height: 375 } });
  await page.goto(baseUrl);

  await page.getByRole("button", { name: "マニュアルを検索" }).click();
  const lastResult = page.locator(".search-result").last();
  await lastResult.scrollIntoViewIfNeeded();

  const resultBox = await lastResult.boundingBox();
  const dialogBox = await page.locator(".search-dialog").boundingBox();
  assert.ok(resultBox);
  assert.ok(dialogBox);
  assert.ok(resultBox.y + resultBox.height <= 375);
  assert.ok(dialogBox.y + dialogBox.height <= 375);
});

test("keeps the participant guide usable on a narrow viewport", async () => {
  const browser = await launchBrowser();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(`${baseUrl}/participant`);

  assert.equal(
    await page.locator("html").evaluate((element) =>
      element.scrollWidth <= element.clientWidth,
    ),
    true,
  );

  await page.getByRole("link", { name: "LINE通知", exact: true }).last().click();
  await page.waitForURL(`${baseUrl}/participant#line-notification`);
  assert.equal(new URL(page.url()).hash, "#line-notification");
  assert.equal(
    await page
      .getByRole("heading", { name: "LINEで組み合わせ通知を受け取る" })
      .isVisible(),
    true,
  );
});
