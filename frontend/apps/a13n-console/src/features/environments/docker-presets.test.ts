import { expect, it } from "vitest";
import { applyDockerPreset, dockerMode } from "./docker-presets";

const presets = {
  standard: { image: "sandbox:reviewed", execution: "native", desktop: false },
  desktop: {
    image: "sandbox:reviewed-desktop",
    execution: "envd_http",
    desktop: true,
  },
};
const schema = {
  properties: {
    image: { default: presets.standard.image },
    execution: { default: "native" },
    desktop: { default: false },
    shell: { default: "/bin/sh" },
  },
};

it("recognizes defaults without silently converting custom images or advanced settings", () => {
  expect(dockerMode({}, schema, presets)).toBe("standard");
  expect(
    dockerMode(
      { ...presets.desktop, cpus: 2, shell: "/bin/sh" },
      schema,
      presets,
    ),
  ).toBe("desktop");
  expect(
    dockerMode(
      { ...presets.desktop, image: "sandbox:local-desktop" },
      schema,
      presets,
    ),
  ).toBe("custom");
  expect(
    dockerMode({ ...presets.standard, shell: "/bin/bash" }, schema, presets),
  ).toBe("custom");
});

it("replaces incompatible custom settings with preset defaults while keeping resources", () => {
  const mounts = [{ source: "/data", target: "/data", read_only: true }];
  expect(
    applyDockerPreset(
      {
        image: "custom",
        user: "1000",
        disable_network: true,
        cpus: 2,
        memory_gb: 4,
        mounts,
      },
      presets.desktop,
    ),
  ).toEqual({ ...presets.desktop, cpus: 2, memory_gb: 4, mounts });
});
