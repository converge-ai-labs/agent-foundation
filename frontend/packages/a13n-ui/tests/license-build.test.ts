import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { expect, it } from "vitest";

const uiRoot = new URL("../", import.meta.url);

it.each([
  "../../../apps/a13n-console/vite.config.ts",
  "../../../apps/a13n-harness-ui/vite.config.ts",
  "../vite.config.ts",
])("preserves Coss notices with %s", async (configuration) => {
  const root = await mkdtemp(join(tmpdir(), "a13n-ui-license-"));
  try {
    await writeFile(
      join(root, "index.html"),
      '<link rel="stylesheet" href="/theme.css"><script type="module" src="/main.js"></script>',
    );
    await writeFile(join(root, "main.js"), 'console.log("license fixture");');
    await writeFile(
      join(root, "theme.css"),
      await readFile(new URL("src/styles/theme.css", uiRoot)),
    );
    const result = await build({
      configFile: fileURLToPath(new URL(configuration, import.meta.url)),
      root,
      publicDir: false,
      logLevel: "silent",
      build: { write: false },
    });
    if (!("output" in result)) throw new Error("Expected one browser build");
    const license = await readFile(new URL("LICENSE.coss", uiRoot), "utf8");
    const notice = result.output.find(
      (file) => file.fileName === "assets/LICENSE.coss",
    );
    expect(notice?.type).toBe("asset");
    if (notice?.type === "asset") expect(notice.source).toBe(license);
    const javascript = result.output.filter((file) => file.type === "chunk");
    expect(javascript.length).toBeGreaterThan(0);
    for (const chunk of javascript)
      expect(chunk.code).toContain(license.trim());
    const stylesheets = result.output
      .filter((file) => file.type === "asset")
      .filter((file) => file.fileName.endsWith(".css"));
    expect(stylesheets.length).toBeGreaterThan(0);
    for (const stylesheet of stylesheets) {
      expect(String(stylesheet.source)).toContain(license.trim());
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
