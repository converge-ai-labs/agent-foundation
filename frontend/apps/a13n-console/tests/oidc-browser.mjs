import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright";

const [directory, phase] = process.argv.slice(2);
const config = JSON.parse(
  await readFile(join(directory, "fixture.json"), "utf8"),
);
const browser = await chromium.launch();
const login = `${config.origin}/login`;
const api = `${config.origin}/api/v1`;
const state = (name) => join(directory, `${name}.json`);
const screenshot = (page, name) =>
  page.screenshot({ path: join(directory, `${name}.png`), fullPage: true });

async function opened(options = {}) {
  const context = await browser.newContext({
    locale: "en-US",
    viewport: { width: 1180, height: 850 },
    ...options,
  });
  const page = await context.newPage();
  await page.goto(login);
  return { context, page };
}

async function session(context) {
  const response = await context.request.get(`${api}/auth/session`);
  assert.equal(response.status(), 200, await response.text());
  return await response.json();
}

async function signInWithDex(page, provider = "Team Identity") {
  await page.getByRole("link", { name: `Sign in with ${provider}` }).click();
  await page.waitForURL(`${config.issuer}/**`);
  const form = page
    .locator("form")
    .filter({ has: page.locator("input[type=password]") });
  await form.locator("input[name=login]").fill(config.email);
  await form.locator("input[type=password]").fill(config.dexPassword);
  await form.locator("button[type=submit]").click();
  await page.waitForURL(
    (url) => url.origin === config.origin && url.pathname !== "/login",
  );
}

try {
  if (phase === "mixed") {
    const admin = await opened();
    await admin.page
      .getByRole("link", { name: "Sign in with Engineering SSO" })
      .waitFor();
    await admin.page.getByLabel("Password", { exact: true }).waitFor();
    await screenshot(admin.page, "mixed-sign-in");
    await admin.page
      .getByLabel("Email address", { exact: true })
      .fill(config.email);
    await admin.page
      .getByLabel("Password", { exact: true })
      .fill(config.localPassword);
    await admin.page
      .getByRole("button", { name: "Sign in", exact: true })
      .click();
    await admin.page.waitForURL((url) => url.pathname !== "/login");
    const local = await session(admin.context);
    assert.equal(local.user.email, config.email);
    await admin.context.storageState({ path: state("old-session") });

    const member = await opened();
    await signInWithDex(member.page);
    const remote = await session(member.context);
    assert.notEqual(remote.user.id, local.user.id);
    assert.equal(remote.user.email, null);
    assert.equal(remote.user.oidc_email, config.email);
    const grants = `${api}/organizations/${config.organization}/grants`;
    const members = `${api}/organizations/${config.organization}/members`;
    const forbidden = await member.context.request.get(grants);
    assert.equal(forbidden.status(), 403);
    const visible = await admin.context.request.get(members);
    assert.equal(visible.status(), 200, await visible.text());
    const person = (await visible.json()).items.find(
      ({ id }) => id === remote.user.id,
    );
    assert.equal(person.oidc_email, config.email);
    const promote = await admin.context.request.post(grants, {
      headers: { "X-CSRF-Token": local.csrf_token },
      data: { principal_id: remote.user.id, role: "admin" },
    });
    assert.equal(promote.status(), 201, await promote.text());

    const verified = await opened();
    await signInWithDex(verified.page, "Engineering SSO");
    assert.equal((await session(verified.context)).user.id, remote.user.id);
    assert.equal((await verified.context.request.get(grants)).status(), 200);
    const key = await verified.context.request.post(`${api}/users/me/keys`, {
      headers: { "X-CSRF-Token": (await session(verified.context)).csrf_token },
      data: { name: "Cutover test", workspace_id: config.workspace },
    });
    assert.equal(key.status(), 201, await key.text());
    await writeFile(
      state("cutover"),
      JSON.stringify({ id: remote.user.id, key: (await key.json()).secret }),
    );
  } else if (phase === "oidc-only") {
    const local = await opened({ storageState: state("old-session") });
    assert.equal(
      (await local.context.request.get(`${api}/auth/session`)).status(),
      401,
    );
    const blocked = await local.context.request.post(`${api}/auth/login`, {
      data: { email: config.email, password: config.localPassword },
    });
    assert.equal(blocked.status(), 403);
    await local.page
      .getByRole("link", { name: "Sign in with Team Identity" })
      .waitFor();
    assert.equal(
      await local.page.getByLabel("Password", { exact: true }).count(),
      0,
    );
    await screenshot(local.page, "oidc-only-sign-in");

    const dark = await opened({
      colorScheme: "dark",
      viewport: { width: 390, height: 700 },
    });
    await dark.page
      .getByRole("link", { name: "Sign in with Engineering SSO" })
      .waitFor();
    assert.ok(
      await dark.page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    await screenshot(dark.page, "oidc-only-mobile-dark");
    for (const [locale, name] of [
      ["zh-CN", "使用 Team Identity 登录"],
      ["hi-IN", "Team Identity से साइन इन करें"],
    ]) {
      const translated = await opened({
        locale,
        viewport: { width: 390, height: 700 },
      });
      await translated.page.getByRole("link", { name }).waitFor();
      await translated.context.close();
    }

    const { id, key } = JSON.parse(await readFile(state("cutover"), "utf8"));
    const keyResult = await local.context.request.get(
      `${api}/workspaces/${config.workspace}`,
      {
        headers: { Authorization: `Bearer ${key}` },
      },
    );
    assert.equal(keyResult.status(), 200, await keyResult.text());
    await signInWithDex(local.page);
    const authenticated = await session(local.context);
    assert.equal(authenticated.user.id, id);
    const update = await local.context.request.patch(`${api}/users/me`, {
      data: { name: "Alex Rivera" },
    });
    assert.equal(update.status(), 403);
    const logout = await local.context.request.post(`${api}/auth/logout`, {
      headers: { "X-CSRF-Token": authenticated.csrf_token },
    });
    assert.equal(logout.status(), 204);
    assert.equal(
      (await local.context.request.get(`${api}/auth/session`)).status(),
      401,
    );
  } else {
    throw new Error(`Unknown phase: ${phase}`);
  }
  console.log(`OIDC browser flow passed: ${phase}`);
} finally {
  await browser.close();
}
