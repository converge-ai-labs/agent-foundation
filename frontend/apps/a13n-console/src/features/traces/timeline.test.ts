import { describe, expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import { observationRows } from "./timeline";
function observation(
  id: string,
  parent_id: string | null,
): Schema["Observation"] {
  return {
    id,
    parent_id,
    type: "span",
    name: id,
    started_at: "2026-09-08T00:00:00Z",
    ended_at: null,
    duration_ms: null,
    status: "unset",
    model: null,
    usage: null,
    cost_usd: null,
    input: null,
    output: null,
    metadata: {},
  };
}
describe("trace topology", () => {
  it("places children after parents regardless of backend ordering and retains orphans", () => {
    expect(
      observationRows([
        observation("child", "root"),
        observation("orphan", "absent"),
        observation("root", null),
      ]).map((row) => [row.observation.id, row.depth]),
    ).toEqual([
      ["orphan", 0],
      ["root", 0],
      ["child", 1],
    ]);
  });
  it("retains cyclic observations without looping or duplicating nodes", () => {
    const rows = observationRows([
      observation("a", "b"),
      observation("b", "a"),
      observation("c", "c"),
    ]);
    expect(rows.map((row) => row.observation.id)).toEqual(["a", "b", "c"]);
    expect(rows[1]?.depth).toBe(1);
    expect(rows[0]?.observation.duration_ms).toBeNull();
  });
});
