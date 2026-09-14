import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import openapiTS, { astToString } from "openapi-typescript";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

// HTTP and interactive frame types share the App-owned OpenAPI components.
const check = process.argv.includes("--check");
const output = check
  ? mkdtempSync(join(tmpdir(), "a13n-harness-ui-contract-"))
  : "src";
try {
  execFileSync(
    "uv",
    [
      "run",
      "--locked",
      "--package",
      "a13n-harness-ui",
      "--no-default-groups",
      "python",
      "../../../scripts/export-a13n-harness-ui-openapi.py",
      "--output",
      `${output}/openapi.json`,
    ],
    { stdio: "inherit" },
  );
  const schema = JSON.parse(readFileSync(`${output}/openapi.json`, "utf8"));
  writeFileSync(
    `${output}/api.generated.ts`,
    astToString(await openapiTS(schema, { defaultNonNullable: false })),
  );
  for (const file of ["openapi.json", "api.generated.ts"]) {
    if (
      check &&
      !readFileSync(`${output}/${file}`).equals(readFileSync(`src/${file}`))
    ) {
      throw new Error(
        `Generated contract drift: ${file}. Run pnpm run generate.`,
      );
    }
  }
} finally {
  if (check) rmSync(output, { recursive: true, force: true });
}
