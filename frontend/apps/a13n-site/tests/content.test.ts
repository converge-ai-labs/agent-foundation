import { expect, test } from "vitest";
import { brandIcon } from "../src/brands";
import { AGENTS, CALLS, EARLIER, STACK } from "../src/content";

test("every brand on the page has a registry icon", () => {
  const names = [
    ...AGENTS.flatMap((a) => [a.model.brand, a.sandbox, ...a.connections]),
    ...STACK.flatMap((row) => row.brands),
    "Docker",
  ];
  for (const name of names) expect(brandIcon(name), name).toMatch(/\S/);
  expect(() => brandIcon("Nobody")).toThrow("No brand icon for Nobody");
});

test("every call and earlier thread starts an agent the sheet shows", () => {
  const agents = new Set(AGENTS.map((a) => a.name));
  for (const call of CALLS) {
    expect(agents).toContain(call.agent);
    for (const task of call.tasks)
      expect(call.code(task).join("\n")).toMatch(/⟨.+⟩/);
  }
  for (const [, agent] of EARLIER) expect(agents).toContain(agent);
});
