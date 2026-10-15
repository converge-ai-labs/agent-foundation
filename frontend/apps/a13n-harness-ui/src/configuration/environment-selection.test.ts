import { expect, it } from "vitest";
import {
  changeLocalRoots,
  invalidEnvironments,
  deviceEnvironments,
  environmentSummary,
  replaceDeviceEnvironment,
  workingDirectories,
  type EnvironmentBinding,
} from "./environment-selection";
import { fullControl, readOnly } from "./environment-permissions";
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

it("keeps an unspecified default when the automatic first directory stays selected", () => {
  for (const default_environment of [undefined, null]) {
    const value = { local_roots: ["/one", "/two"], default_environment };
    for (const roots of [
      ["/one", "/two", "/three"],
      ["/one"],
      ["/renamed", "/two"],
    ])
      expect(changeLocalRoots(value, roots)).toEqual({ local_roots: roots });
    expect(changeLocalRoots(value, ["/two", "/one"])).toEqual({
      local_roots: ["/two", "/one"],
      default_environment: "workspace-2",
    });
  }
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

const remote: EnvironmentBinding = {
  device_id: "device-build",
  alias: "build",
  working_directory: "/work",
};

it("counts working environments independently of directories and auxiliary files", () => {
  expect(environmentSummary({ local_roots: ["/one", "/two"] })).toBe(
    "Local · 2 directories",
  );
  expect(environmentSummary({})).toBe("Local · Thread files");
  const bindings = [
    remote,
    { ...remote, alias: "notes", working_directory: "/notes" },
  ];
  expect(
    environmentSummary({
      local_roots: ["/one", "/two"],
      environment_bindings: bindings,
    }),
  ).toBe("2 environments · 4 directories");
  expect(environmentSummary({ environment_bindings: bindings })).toBe(
    "1 environment · 2 directories",
  );
});

it("groups only equal Device policies without mutating legacy selections", () => {
  const first = { ...remote, permission_ceiling: { operations: fullControl } };
  const second = {
    ...first,
    alias: "notes",
    working_directory: "/notes",
    permission_ceiling: { operations: [...fullControl].reverse() },
  };
  const others: EnvironmentBinding[] = [
    remote,
    { ...remote, alias: "read", permission_ceiling: { operations: readOnly } },
    { ...remote, alias: "none", permission_ceiling: { operations: [] } },
    { ...remote, alias: "other", device_id: "device-other" },
    {
      ...remote,
      alias: "network",
      egress: { destinations: { mode: "allowlist", hosts: ["example.com"] } },
    },
    {
      ...remote,
      alias: "boundary",
      expected_boundary: { sandbox: { mode: "disabled" }, egress: "inherit" },
    },
  ];
  const bindings = [first, ...others, second];
  const saved = JSON.stringify(bindings);
  const groups = deviceEnvironments(bindings);
  expect(groups).toHaveLength(7);
  expect(groups[0].directories).toEqual([first, second]);
  expect(groups[0].directories[0]).toBe(first);
  expect(JSON.stringify(bindings)).toBe(saved);
});

it("round-trips legacy binding order and retargets only an explicitly renamed default", () => {
  const other = { ...remote, device_id: "device-other", alias: "other" };
  const second = { ...remote, alias: "notes", working_directory: "/notes" };
  const bindings = [remote, other, second];
  const [group] = deviceEnvironments(bindings);
  expect(
    replaceDeviceEnvironment(
      bindings,
      group.directories,
      group.directories,
      "notes",
    ),
  ).toEqual({
    environment_bindings: bindings,
    default_environment: "notes",
  });
  const next = [
    remote,
    { ...second, alias: "references" },
    { ...remote, alias: "third", working_directory: "/third" },
  ];
  expect(
    replaceDeviceEnvironment(bindings, group.directories, next, "notes"),
  ).toEqual({
    environment_bindings: [remote, other, next[1], next[2]],
    default_environment: "references",
  });
});

it("shows readable directory choices while keeping routing identities internal", () => {
  const choices = workingDirectories(
    { local_roots: ["/one", "/two"], environment_bindings: [remote] },
    [{ id: "device-build", name: "Build machine" }],
  );
  expect(choices).toEqual([
    { value: "workspace", label: "Local · /one" },
    { value: "workspace-2", label: "Local · /two" },
    { value: "thread-files", label: "Local · Thread files" },
    { value: "build", label: "Build machine · /work" },
  ]);
  expect(changeLocalRoots({ local_roots: ["/one", "/two"] }, ["/two"])).toEqual(
    { local_roots: ["/two"], default_environment: "workspace-0" },
  );
});
