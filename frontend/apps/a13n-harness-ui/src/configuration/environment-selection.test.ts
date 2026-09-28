import { expect, it } from "vitest";
import { changeLocalRoots, invalidEnvironments } from "./environment-selection";
import { connectCommand } from "./device-command";

it("preserves selected local directory identity across reordering and removal", () => {
  const value = {
    local_roots: ["/one", "/two", "/three"],
    default_environment: "workspace-2",
  };
  expect(changeLocalRoots(value, ["/two", "/three"])).toEqual({
    local_roots: ["/two", "/three"],
    default_environment: "workspace",
  });
  expect(
    changeLocalRoots(value, ["/three", "/one", "/two"]).default_environment,
  ).toBe("workspace-3");
  const removed = { ...value, ...changeLocalRoots(value, ["/one", "/three"]) };
  expect(invalidEnvironments(removed)).toBe(true);
  expect(
    invalidEnvironments({ ...removed, default_environment: "thread-files" }),
  ).toBe(false);
  expect(
    changeLocalRoots({ ...value, default_environment: "build" }, []),
  ).toEqual({ local_roots: [] });
});

it("requires a valid explicit default for Devices and at least one Project directory", () => {
  const remote = {
    local_roots: [],
    environment_bindings: [
      { alias: "build", device_id: "device-build", working_directory: "/work" },
    ],
  };
  expect(invalidEnvironments(remote, true)).toBe(true);
  expect(
    invalidEnvironments({ ...remote, default_environment: "build" }, true),
  ).toBe(false);
  expect(invalidEnvironments({ local_roots: [] })).toBe(false);
  expect(invalidEnvironments({ local_roots: [] }, true)).toBe(true);
  expect(invalidEnvironments({ local_roots: ["/one", "/one"] })).toBe(true);
  expect(invalidEnvironments({ local_roots: [""] })).toBe(true);
});

it("generates explicit independent shell and desktop grants for each terminal", () => {
  expect(connectCommand("https://ui.example", "posix", false, false)).toBe(
    "A13N_ENVD_FULL_CONTROL=0 a13n-envd connect 'https://ui.example' --computer-use false",
  );
  expect(connectCommand("https://ui.example", "posix", true, false)).toBe(
    "A13N_ENVD_FULL_CONTROL=1 a13n-envd connect 'https://ui.example' --computer-use false",
  );
  expect(connectCommand("https://ui.example", "powershell", true, true)).toBe(
    "& { $previous = $env:A13N_ENVD_FULL_CONTROL; try { $env:A13N_ENVD_FULL_CONTROL='1'; a13n-envd connect 'https://ui.example' --computer-use true } finally { $env:A13N_ENVD_FULL_CONTROL=$previous } }",
  );
  expect(connectCommand("https://ui.example", "powershell", false, false)).toBe(
    "& { $previous = $env:A13N_ENVD_FULL_CONTROL; try { $env:A13N_ENVD_FULL_CONTROL='0'; a13n-envd connect 'https://ui.example' --computer-use false } finally { $env:A13N_ENVD_FULL_CONTROL=$previous } }",
  );
  expect(connectCommand("https://ui.example", "posix", false, true)).toContain(
    "A13N_ENVD_FULL_CONTROL=0",
  );
});
