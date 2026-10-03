import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import {
  applyItemChanges,
  type CompactItem,
  type ItemChange,
} from "../src/lib/display";

// The Python semantic owner verifies the exact same source events and changes.
const fixture = JSON.parse(
  readFileSync(
    new URL(
      "../../../../packages/a13n-stream-protocol/tests/fixtures/compact-display.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as { batches: ItemChange<CompactItem>[][]; items: CompactItem[] };

it("reconstructs the Python compact display and deduplicates replay at every cut", () => {
  for (let cut = 0; cut <= fixture.batches.length; cut++) {
    const items = new Map<string, CompactItem>();
    for (const batch of fixture.batches.slice(0, cut))
      expect(applyItemChanges(items, batch)).toBe(true);
    const restored = new Map(
      JSON.parse(JSON.stringify([...items])) as [string, CompactItem][],
    );
    // A transport may repeat all retained deltas, including the checkpoint prefix.
    for (const batch of fixture.batches)
      expect(applyItemChanges(restored, batch)).toBe(true);
    expect([...restored.values()]).toEqual(fixture.items);
  }
});

it("rejects a batch atomically when an append has no baseline", () => {
  const items = new Map<string, CompactItem>();
  const append = fixture.batches
    .flat()
    .find((change) => change.type === "append")!;
  expect(
    applyItemChanges(items, [
      fixture.batches[0]![0]!,
      { ...append, id: "missing" },
    ]),
  ).toBe(false);
  expect(items.size).toBe(0);
});
