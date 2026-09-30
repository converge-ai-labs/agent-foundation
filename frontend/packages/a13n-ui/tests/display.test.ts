// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  DisplayGap,
  DisplayState,
  type DisplayDelta,
  type DisplaySnapshot,
} from "../src/lib/display";

const fixtures = JSON.parse(
  readFileSync(
    new URL(
      "../../../../packages/a13n-stream-protocol/tests/display-fixtures.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  baseline: DisplaySnapshot;
  accepted: { name: string; delta: DisplayDelta; snapshot: DisplaySnapshot }[];
  invalid: { name: string; delta: DisplayDelta }[];
};

function completed(): DisplayState {
  const state = new DisplayState(fixtures.baseline);
  for (const entry of fixtures.accepted) state.apply(entry.delta);
  return state;
}

describe("shared Python/TypeScript display fixtures", () => {
  it("applies identical atomic operations and ignores covered duplicates", () => {
    const state = new DisplayState(fixtures.baseline);
    for (const entry of fixtures.accepted) {
      expect(state.apply(entry.delta), entry.name).toBe(true);
      expect(state.capture(), entry.name).toEqual(entry.snapshot);
      expect(state.apply(entry.delta), entry.name).toBe(false);
    }
  });

  for (const entry of fixtures.invalid) {
    it(`rejects atomically: ${entry.name}`, () => {
      const state = completed();
      const before = state.capture();
      expect(() => state.apply(entry.delta)).toThrow(DisplayGap);
      expect(state.capture()).toEqual(before);
    });
  }

  it("rejects gaps and new attempts until an authoritative baseline is installed", () => {
    const state = new DisplayState(fixtures.baseline);
    expect(() => state.apply(fixtures.accepted[1]!.delta)).toThrow(DisplayGap);
    const other = structuredClone(fixtures.accepted[0]!.delta);
    other.producer.generation = "attempt-2";
    expect(() => state.apply(other)).toThrow(DisplayGap);
    expect(state.capture()).toEqual(fixtures.baseline);
  });

  it("keeps captures and restores detached from callers", () => {
    const state = completed();
    const snapshot = state.capture();
    snapshot.blocks[0]!.content.arguments = "changed";
    expect(state.capture().blocks[0]!.content.arguments).toBe("{}");
    const restored = new DisplayState(snapshot);
    snapshot.blocks[0]!.content.arguments = "changed again";
    expect(restored.capture().blocks[0]!.content.arguments).toBe("changed");
  });
});
