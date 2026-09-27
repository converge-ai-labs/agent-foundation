import { readFileSync } from "node:fs";
import type { Plugin, UserConfig } from "vite";

/** Preserve the vendored Coss sources' license in every browser distribution. */
export function cossLicense(): Plugin {
  const license = readFileSync(
    new URL("./LICENSE.coss", import.meta.url),
    "utf8",
  );
  return {
    name: "a13n-ui-coss-license",
    apply: "build",
    config(): UserConfig {
      return {
        css: {
          postcss: {
            plugins: [
              {
                postcssPlugin: "a13n-ui-coss-license",
                OnceExit(root) {
                  root.prepend({
                    text: `!\n${license.trim()}\n`,
                    raws: { left: "", right: "" },
                  });
                },
              },
            ],
          },
        },
        build: {
          rolldownOptions: {
            output: { postBanner: `/*!\n${license.trim()}\n*/` },
          },
        },
      };
    },
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "assets/LICENSE.coss",
        source: license,
      });
    },
  };
}
