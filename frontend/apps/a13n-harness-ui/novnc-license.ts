import { readFileSync } from "node:fs";
import type { Plugin } from "vite";

/** Include upstream notices and corresponding source for the bundled RFB client. */
export function novncLicense(): Plugin {
  const root = new URL("../", import.meta.resolve("@novnc/novnc"));
  const { version } = JSON.parse(
    readFileSync(new URL("package.json", root), "utf8"),
  ) as { version: string };
  const source = [
    `noVNC ${version} — unmodified upstream library`,
    `Corresponding source: https://github.com/novnc/noVNC/tree/v${version}`,
    ...[
      "AUTHORS",
      "LICENSE.txt",
      "docs/LICENSE.MPL-2.0",
      "docs/LICENSE.BSD-2-Clause",
      "docs/LICENSE.BSD-3-Clause",
      "vendor/pako/LICENSE",
    ].map(
      (name) => `\n${name}\n\n${readFileSync(new URL(name, root), "utf8")}`,
    ),
  ].join("\n");
  return {
    name: "a13n-harness-ui-novnc-license",
    apply: "build",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "assets/LICENSE.noVNC",
        source,
      });
    },
  };
}
