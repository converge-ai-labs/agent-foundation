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

async function session(context, { absolute = true } = {}) {
  const response = await context.request.get(`${api}/auth/session`);
  assert.equal(response.status(), 200, await response.text());
  if (absolute) assert.equal(response.headers()["set-cookie"], undefined);
  return await response.json();
}

async function forbidden(response, message = "Password sign-in is disabled") {
  assert.equal(response.status(), 403, await response.text());
  const { error } = await response.json();
  assert.equal(error.code, "forbidden");
  assert.equal(error.message, message);
}

async function signInWithDex(page, provider = "Team Identity") {
  await page.getByRole("link", { name: `Sign in with ${provider}` }).click();
  await page.waitForURL(`${config.issuer}/**`);
  const form = page
    .locator("form")
    .filter({ has: page.locator("input[type=password]") });
  await form.locator("input[name=login]").fill(config.email);
  await form.locator("input[type=password]").fill(config.dexPassword);
  const callback = page.waitForResponse((response) =>
    /^\/api\/v1\/auth\/oidc\/[^/]+\/callback$/.test(
      new URL(response.url()).pathname,
    ),
  );
  await form.locator("button[type=submit]").click();
  const cookies = await (await callback).headerValues("set-cookie");
  assert.match(
    cookies.find((cookie) => cookie.startsWith("a13n_session=")) ?? "",
    /; Max-Age=900(?:;|$)/i,
  );
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
    for (const brand of ["Google", "Microsoft"]) {
      const image = admin.page
        .getByRole("link", { name: `Sign in with ${brand}` })
        .locator("img");
      assert.ok(
        await image.evaluate(async (img) => {
          await img.decode();
          return (
            img.currentSrc.startsWith("data:image/") ||
            new URL(img.currentSrc).origin === location.origin
          );
        }),
      );
    }
    assert.equal(
      await admin.page
        .getByRole("link", { name: "Sign in with Team Identity" })
        .locator("img")
        .count(),
      0,
    );
    const google = admin.page.getByRole("link", {
      name: "Sign in with Google",
    });
    const microsoft = admin.page.getByRole("link", {
      name: "Sign in with Microsoft",
    });
    const keyboardFocused = (link) =>
      link.evaluate((element) => element.matches(":focus-visible"));
    for (let i = 0; i < 5 && !(await keyboardFocused(google)); i++) {
      await admin.page.keyboard.press("Tab");
    }
    assert.ok(await keyboardFocused(google));
    await admin.page.keyboard.press("Tab");
    assert.ok(await keyboardFocused(microsoft));
    await admin.page.locator("h1").click();
    await screenshot(admin.page, "mixed-sign-in");

    const retry = await opened();
    const team = retry.page.getByRole("link", {
      name: "Sign in with Team Identity",
    });
    // Let React handle the first click, but keep the document available to
    // inspect the pending state. The separate Dex flow follows the real link.
    await team.evaluate((link) => {
      window.addEventListener("click", (event) => event.preventDefault(), {
        once: true,
      });
      link.click();
    });
    assert.equal(await team.getAttribute("aria-busy"), "true");
    const engineering = retry.page.getByRole("link", {
      name: "Sign in with Engineering SSO",
    });
    assert.equal(await engineering.getAttribute("aria-disabled"), "true");
    assert.equal(
      await engineering.evaluate((link) =>
        link.dispatchEvent(
          new MouseEvent("click", { cancelable: true, bubbles: true }),
        ),
      ),
      false,
    );
    await retry.page.goto(`${api}/auth/oidc/team/callback`);
    await retry.page
      .getByText(
        "Sign-in didn’t complete. Try again or contact your administrator.",
      )
      .waitFor();
    assert.equal(await team.getAttribute("aria-disabled"), null);
    await retry.context.close();

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
    const local = await session(admin.context, { absolute: false });
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
    const deniedGrantList = await member.context.request.get(grants);
    assert.equal(deniedGrantList.status(), 403);
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
    const headers = { "X-CSRF-Token": local.csrf_token };
    const invitation = await admin.context.request.post(
      `${api}/organizations/${config.organization}/invitations`,
      {
        headers,
        data: { email: "invitee@example.com", role: "runner" },
      },
    );
    assert.equal(invitation.status(), 201, await invitation.text());
    const receipt = await invitation.json();
    const invitationToken = new URLSearchParams(
      new URL(receipt.invitation_url).hash.slice(1),
    ).get("token");
    assert.ok(invitationToken);
    const key = await admin.context.request.post(`${api}/users/me/keys`, {
      headers,
      data: { name: "Cutover test", workspace_id: config.workspace },
    });
    assert.equal(key.status(), 201, await key.text());
    await writeFile(
      state("cutover"),
      JSON.stringify({
        id: remote.user.id,
        key: (await key.json()).secret,
        invitationId: receipt.invitation.id,
        invitationToken,
      }),
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
    await forbidden(blocked);
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

    const { id, key, invitationId, invitationToken } = JSON.parse(
      await readFile(state("cutover"), "utf8"),
    );
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
    const request = local.context.request;
    const csrf = { "X-CSRF-Token": authenticated.csrf_token };
    const profile = await request.get(`${api}/users/me`);
    assert.equal(profile.status(), 200);
    const etag = profile.headers().etag;
    assert.ok(etag);
    const guarded = { ...csrf, "If-Match": etag };
    await forbidden(
      await request.patch(`${api}/users/me`, {
        headers: { "If-Match": etag },
        data: { name: "Alex Rivera" },
      }),
      "CSRF validation failed",
    );
    await forbidden(
      await request.patch(`${api}/users/me`, {
        headers: guarded,
        data: {
          email: "alternate@example.com",
          current_password: config.localPassword,
        },
      }),
    );
    const nameUpdate = await request.patch(`${api}/users/me`, {
      headers: guarded,
      data: { name: "Alex Updated" },
    });
    assert.equal(nameUpdate.status(), 200, await nameUpdate.text());
    assert.equal((await nameUpdate.json()).name, "Alex Updated");

    for (const [path, data] of [
      [
        "auth/bootstrap",
        { email: config.email, password: config.localPassword },
      ],
      ["auth/password-reset", { email: config.email }],
      [
        "auth/password-reset/confirm",
        { token: invitationToken, password: config.localPassword },
      ],
      ["auth/email-change/confirm", { token: invitationToken }],
      [
        `invitations/${invitationId}/accept`,
        { token: invitationToken, password: config.localPassword },
      ],
      [
        "users/me/password",
        {
          current_password: config.localPassword,
          password: "updated-fixture-password",
        },
      ],
    ]) {
      await forbidden(
        await request.post(`${api}/${path}`, { headers: csrf, data }),
      );
    }
    const invitations = `${api}/organizations/${config.organization}/invitations`;
    await forbidden(
      await request.post(invitations, {
        headers: csrf,
        data: { email: "another-invitee@example.com", role: "runner" },
      }),
      "Password invitations are disabled",
    );
    await forbidden(
      await request.post(`${invitations}/${invitationId}/resend`, {
        headers: csrf,
      }),
      "Password invitations are disabled",
    );

    const logout = await request.post(`${api}/auth/logout`, { headers: csrf });
    assert.equal(logout.status(), 204);
    assert.equal(
      (await local.context.request.get(`${api}/auth/session`)).status(),
      401,
    );
  } else if (phase === "single") {
    assert.equal(config.longLabel.length, 64);
    const { page } = await opened({
      colorScheme: "dark",
      viewport: { width: 320, height: 600 },
    });
    const link = page.getByRole("link", {
      name: `Sign in with ${config.longLabel}`,
    });
    await link.waitFor();
    assert.equal(await link.getAttribute("data-variant"), "default");
    assert.equal(await page.getByLabel("Password", { exact: true }).count(), 0);
    assert.ok(
      await link.evaluate((element) => {
        const text = element.children[1];
        const lineHeight = Number.parseFloat(getComputedStyle(text).lineHeight);
        return (
          text.clientHeight > lineHeight &&
          text.scrollWidth === text.clientWidth &&
          document.documentElement.scrollWidth <= window.innerWidth
        );
      }),
    );
    await screenshot(page, "single-provider-mobile-dark");
  } else {
    throw new Error(`Unknown phase: ${phase}`);
  }
  console.log(`OIDC browser flow passed: ${phase}`);
} finally {
  await browser.close();
}
