import { expect, it } from "vitest";
import { parse } from "yaml";
import { readDocument, updateDocument, template } from "./documents";

it("edits structured fields without dropping comments or unknown source fields", () => {
  const text =
    '# user comment\nschema_version: "1"\nkind: agent\nname: Original # name note\ninstructions: |\n  Keep my instructions.\ncapabilities:\n  - capability: custom\n    configuration:\n      future: true\n';
  const next = updateDocument(text, ["name"], "Revised");
  expect(next).toContain("# user comment");
  expect(next).toContain("# name note");
  expect(parse(next)).toEqual({ ...parse(text), name: "Revised" });
});
it("preserves inherited, explicitly empty and custom list axes", () => {
  const source = template("project", "project-test");
  const none = updateDocument(source, ["defaults", "mcp_servers"], []);
  expect(parse(none).defaults.mcp_servers).toEqual([]);
  const custom = updateDocument(none, ["defaults", "mcp_servers"], ["mcp-one"]);
  expect(parse(custom).defaults.mcp_servers).toEqual(["mcp-one"]);
  const inherited = updateDocument(
    custom,
    ["defaults", "mcp_servers"],
    undefined,
  );
  expect(parse(inherited).defaults).not.toHaveProperty("mcp_servers");
});
it("does not guess a document on invalid or markdown input", () => {
  expect(readDocument("not: [valid")).toBeNull();
  expect(readDocument("plain instructions")).toBeNull();
  expect(() => updateDocument("not: [valid", ["name"], "new")).toThrow();
});
