import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageRoot = new URL("../", import.meta.url);

test("package can be imported without exposing a premature API", async () => {
  const sdk = await import("../dist/index.js");

  assert.deepEqual(Object.keys(sdk), []);
});

test("package metadata identifies the public npm package", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("package.json", packageRoot), "utf8"),
  );

  assert.equal(packageJson.name, "a13n");
  assert.equal(packageJson.publishConfig.access, "public");
  assert.equal(packageJson.private, undefined);
});
