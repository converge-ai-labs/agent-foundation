import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageRoot = new URL("../", import.meta.url);

test("package exposes a typed Service client", async () => {
  const sdk = await import("../dist/index.js");

  assert.equal(typeof sdk.createClient, "function");
  assert.equal(typeof sdk.ApiError, "function");
});

test("package metadata identifies the public npm package", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("package.json", packageRoot), "utf8"),
  );

  assert.equal(packageJson.name, "@converge.ai/a13n");
  assert.equal(packageJson.publishConfig.access, "public");
  assert.equal(packageJson.private, undefined);
});
