import { expect, it } from "vitest";
import { fixtureThread } from "./fixture";
import { childThreadOf } from "./thread-runs";

it("keeps delegation links on child threads when the same Run also has conversation forks", () => {
  const fork = fixtureThread({ origin: "fork", origin_run_id: "run_parent" });
  const child = fixtureThread({ origin: "child", origin_run_id: "run_parent" });
  expect(childThreadOf([fork, child], "run_parent")).toBe(child);
  expect(childThreadOf([fork], "run_parent")).toBeNull();
});
