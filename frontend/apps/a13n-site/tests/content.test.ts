import { expect, test } from "vitest";
import { brandIcon } from "../src/brands";
import { AGENTS, STACK } from "../src/content";

test("every brand on the page has a registry icon", () => {
  const names = [
    ...AGENTS.flatMap((a) => [a.model.brand, a.sandbox, ...a.connections]),
    ...STACK.flatMap((row) => row.brands),
    "Docker",
  ];
  for (const name of names) expect(brandIcon(name), name).toMatch(/\S/);
  expect(() => brandIcon("Nobody")).toThrow("No brand icon for Nobody");
});
