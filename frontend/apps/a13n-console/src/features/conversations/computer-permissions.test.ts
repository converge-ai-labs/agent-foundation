// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  computerActions,
  computerPermissionMode,
  observationActions,
} from "./computer-permissions";

describe("Computer permission presets", () => {
  it("includes semantic reads in Observe and exact native mutations in Interact", () => {
    expect(observationActions).toHaveLength(10);
    expect(computerActions).toHaveLength(99);
    expect(new Set(computerActions).size).toBe(computerActions.length);
    expect(observationActions).toContain(
      "environment.computer.a11y_range_read",
    );
    expect(
      observationActions.every((action) => computerActions.includes(action)),
    ).toBe(true);
    expect(computerActions).toContain("environment.computer.atspi_text_insert");
    expect(computerActions).toContain("environment.computer.uia_invoke");
    expect(computerActions).toContain("environment.computer.ax_perform_action");
    expect(
      observationActions.every(
        (action) =>
          !action.includes(".atspi_") &&
          !action.includes(".uia_") &&
          !action.includes(".ax_"),
      ),
    ).toBe(true);
    expect(computerPermissionMode(observationActions)).toBe("observe");
    expect(computerPermissionMode(computerActions)).toBe("interact");
  });

  it("preserves exact partial and legacy permission sets as Custom", () => {
    expect(computerPermissionMode(["environment.file.read_bytes"])).toBe("off");
    expect(computerPermissionMode(["environment.computer.observe"])).toBe(
      "custom",
    );
    expect(computerPermissionMode(["environment.computer.uia_invoke"])).toBe(
      "custom",
    );
    expect(
      computerPermissionMode([
        ...observationActions,
        "environment.file.read_bytes",
      ]),
    ).toBe("observe");
  });
});
