import { expect, it } from "vitest";
import { i18n } from "../../i18n";
import { statusLabels } from "./labels";

it("labels every Environment lifecycle status and ignores object prototype names", () => {
  const labels = statusLabels(i18n.getFixedT("zh-CN"));
  for (const status of [
    "reserved",
    "creating",
    "starting",
    "ready",
    "stopping",
    "stopped",
    "deleting",
    "deleted",
  ]) {
    expect(Object.hasOwn(labels, status)).toBe(true);
    expect(labels[status]).not.toBe(status);
  }
  expect(labels.constructor).toBeUndefined();
  expect(labels.toString).toBeUndefined();
});
