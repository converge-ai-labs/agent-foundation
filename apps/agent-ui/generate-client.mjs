import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

// Retain the backend contract snapshot without generating an unused client.
const check = process.argv.includes("--check");
const output = check ? mkdtempSync(join(tmpdir(), "a13n-ui-contract-")) : "src";
try {
  execFileSync(
    "uv",
    [
      "run",
      "--locked",
      "--package",
      "a13n-ui",
      "--no-default-groups",
      "python",
      "../../scripts/export-agent-ui-openapi.py",
      "--output",
      `${output}/openapi.json`,
    ],
    { stdio: "inherit" },
  );
  if (
    check &&
    !readFileSync(`${output}/openapi.json`).equals(
      readFileSync("src/openapi.json"),
    )
  ) {
    throw new Error(
      "Generated contract drift: openapi.json. Run npm run generate.",
    );
  }
} finally {
  if (check) rmSync(output, { recursive: true, force: true });
}
