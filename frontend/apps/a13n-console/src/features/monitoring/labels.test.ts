import { expect, it } from "vitest";
import { i18n } from "../../i18n";
import { statusLabels, unitOptions } from "./labels";

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

it("translates every monitoring unit and status through catalogued literal keys", () => {
  const en = i18n.getFixedT("en"),
    zh = i18n.getFixedT("zh-CN");
  const english = [
    ...unitOptions(en).map((option) => option.label),
    ...Object.values(statusLabels(en)),
  ];
  const chinese = [
    ...unitOptions(zh).map((option) => option.label),
    ...Object.values(statusLabels(zh)),
  ];
  expect(english).toHaveLength(chinese.length);
  english.forEach((label, index) => {
    expect(i18n.exists(label, { lng: "en" })).toBe(true);
    expect(i18n.exists(label, { lng: "zh-CN" })).toBe(true);
    expect(chinese[index]).not.toBe(label);
  });
  for (const key of [
    "New panel",
    "Header names must be nonempty and unique, and each header needs a value.",
    "Select an Environment or Device to query metrics.",
  ]) {
    expect(i18n.exists(key, { lng: "en" })).toBe(true);
    expect(zh(key)).not.toBe(key);
  }
});
