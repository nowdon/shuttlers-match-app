import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { after, before, test } from "node:test";
import { chromium } from "playwright-core";

const port = 4174;
const baseUrl = `http://127.0.0.1:${port}`;
const browsers = new Set();
let chromiumArgs;
let chromiumPath;
let server;

function waitForExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return new Promise((resolve) => child.once("exit", resolve));
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

async function stopServer() {
  if (!server) return;
  const exited = waitForExit(server);
  killServerGroup("SIGTERM");
  let timeout;
  const stopped = await Promise.race([
    exited.then(() => true),
    new Promise((resolve) => {
      timeout = setTimeout(() => resolve(false), 5_000);
    }),
  ]);
  clearTimeout(timeout);
  if (!stopped) {
    killServerGroup("SIGKILL");
    await exited;
  }
}

async function waitForServer() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {
      // The development server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Manual site development server did not start");
}

before(async () => {
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
      env: { ...process.env, NO_COLOR: "1" },
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
