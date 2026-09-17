import { expect, it } from "vitest";
import { parse } from "yaml";
import { toggleNativeTool } from "./native-tools";
import type { Schema } from "../transport/client";

const search: Schema<"ModelToolChoice"> = {
  value: "web_search",
  label: "Search",
  description: "Native search",
  recommended: true,
  replaces_host_operation: "search",
  capability: {
    capability: "NativeTool",
    configuration: { kind: "web_search", external_web_access: true },
  },
};
it("toggles one native tool without replacing custom tools, web parameters or comments", () => {
  const source =
    "# Agent\ncapabilities:\n  - capability: web\n    configuration:\n      # keep proxy\n      search: {mode: host, extra: retained}\n      scrape: {mode: host}\n      download: {enabled: true}\n  - capability: NativeTool\n    configuration: {kind: code_execution, provider_option: retained}\n";
  const enabled = toggleNativeTool(source, search, true);
  expect(enabled).toContain("# keep proxy");
  expect(parse(enabled).capabilities[0].configuration).toEqual({
    search: { mode: "off", extra: "retained" },
    scrape: { mode: "host" },
    download: { enabled: true },
  });
  expect(parse(enabled).capabilities[1]).toEqual(parse(source).capabilities[1]);
  expect(parse(enabled).capabilities[2]).toEqual(search.capability);
  const disabled = toggleNativeTool(enabled, search, false);
  expect(parse(disabled)).toEqual(parse(source));
});
it("does not duplicate a configured tool and can author an empty capability list", () => {
  const once = toggleNativeTool("capabilities: []\n", search, true);
  expect(parse(toggleNativeTool(once, search, true))).toEqual(parse(once));
  expect(parse(once).capabilities).toHaveLength(2);
});
