import { execFileSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { rollup } from "rollup";
import { dts } from "rollup-plugin-dts";
import ts from "typescript";

// App projects resolve these as development dependencies, not bundled runtimes.
const typeDependencies = new Set([
  "@tanstack/react-query",
  "class-variance-authority",
  "class-variance-authority/types",
  "react",
  "react-i18next",
  "react-router",
]);
function isTypeDependency(id) {
  return typeDependencies.has(id) || id.startsWith("@base-ui/react/");
}

const require = createRequire(import.meta.url);
const declarations = await mkdtemp(join(tmpdir(), "a13n-console-sdk-"));
try {
  execFileSync(
    process.execPath,
    [
      require.resolve("typescript/bin/tsc"),
      "-p",
      "tsconfig.sdk.json",
      "--outDir",
      declarations,
    ],
    { stdio: "inherit" },
  );
  const bundle = await rollup({
    input: join(
      declarations,
      "frontend/apps/a13n-console/src/app-plugins/bridge.d.ts",
    ),
    external: (id) =>
      id.endsWith(".css") ||
      (!id.startsWith(".") && !id.startsWith("/") && id !== "a13n-ui"),
    plugins: [
      {
        name: "workspace-ui-declarations",
        resolveId(id) {
          if (id === "a13n-ui")
            return join(
              declarations,
              "frontend/packages/a13n-ui/src/index.d.ts",
            );
        },
      },
      dts({ respectExternal: true }),
      {
        name: "standalone-console-sdk",
        generateBundle(_options, output) {
          for (const chunk of Object.values(output)) {
            if (chunk.type !== "chunk") continue;
            for (const { fileName } of ts.preProcessFile(chunk.code, true, true)
              .importedFiles) {
              if (!isTypeDependency(fileName)) {
                this.error(
                  `Console SDK references '${fileName}' outside its public type dependencies`,
                );
              }
            }
          }
        },
      },
    ],
  });
  try {
    await bundle.write({ file: "dist/console-sdk/index.d.ts", format: "es" });
  } finally {
    await bundle.close();
  }
} finally {
  await rm(declarations, { recursive: true, force: true });
}
