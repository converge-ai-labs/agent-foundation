import { mcpServers } from "a13n-mcp-directory";
import { resolveBrand } from "a13n-ui";
import { expect, it } from "vitest";
import { mcpPresets } from "./presets";

it("resolves every Remote MCP preset through the shared brand registry", () => {
  expect(mcpPresets).toHaveLength(Object.keys(mcpServers).length);
  for (const preset of mcpPresets) {
    expect(preset.endpoint, preset.id).toBe(mcpServers[preset.id].endpoint);
    const identity = resolveBrand({ identity: preset.id });
    expect(identity, preset.id).toBeDefined();
    expect(resolveBrand({ endpoint: preset.endpoint }), preset.endpoint).toBe(
      identity,
    );
  }
});
