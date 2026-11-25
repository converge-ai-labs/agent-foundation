import { expect, it } from "vitest";
import {
  creationNotice,
  HEADLESS_INSTALL_NOTICE,
  HEADLESS_IMAGE_NOTICE,
  HEADLESS_SNAPSHOT_NOTICE,
  VERCEL_FIRST_DESKTOP_NOTICE,
} from "./creation-progress";

it.each([
  [
    "e2b",
    { template: "base", preparation: "headless" },
    HEADLESS_INSTALL_NOTICE,
  ],
  ["modal", { image: "auto" }, HEADLESS_IMAGE_NOTICE],
  ["daytona", { snapshot: "auto" }, HEADLESS_SNAPSHOT_NOTICE],
  ["vercel", {}, VERCEL_FIRST_DESKTOP_NOTICE],
  ["sprites", {}, HEADLESS_INSTALL_NOTICE],
  ["vercel", { snapshot: "snap_custom" }, undefined],
  ["modal", { image: "im_custom" }, undefined],
  ["e2b", { template: "custom", preparation: "preinstalled" }, undefined],
  ["docker", {}, undefined],
] as const)(
  "selects the %s preparation notice from provider and recipe",
  (type, recipe, notice) => {
    expect(creationNotice(type, { execution: "envd_http", ...recipe })).toBe(
      notice,
    );
  },
);
