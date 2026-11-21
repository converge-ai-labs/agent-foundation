import { expect, it } from "vitest";
import { appText, settingsKey, validateContribution } from "./types";
const component = () => null;
it("namespaces pages and settings without taking over core routes", () => {
  const contribution = {
    bridge: 1 as const,
    pages: [{ id: "overview", label: "Reports", component }],
    settings: [
      {
        id: "reporting",
        label: "Reporting",
        scope: "workspace" as const,
        component,
      },
      {
        id: "reporting",
        label: "Reporting",
        scope: "organization" as const,
        component,
      },
    ],
  };
  expect(validateContribution(contribution)).toBe(contribution);
  expect(settingsKey("acme.reports", "reporting")).toBe(
    "app.acme.reports.reporting",
  );
  expect(appText({ en: "Reports", "zh-CN": "报告" }, "zh-CN")).toBe("报告");
  expect(appText({ en: "Reports" }, "zh-CN")).toBe("Reports");
});
it.each([
  null,
  42,
  {},
  { en: { title: "Reports" } },
  { en: "Reports", "zh-CN": 42 },
])("rejects malformed text metadata %j before shell rendering", (text) => {
  for (const field of ["label", "description"] as const) {
    const page = { id: "overview", label: "Reports", component, [field]: text };
    expect(() =>
      validateContribution({ bridge: 1, pages: [page] } as never),
    ).toThrow(/invalid.*text/);
    expect(() =>
      validateContribution({
        bridge: 1,
        settings: [{ ...page, scope: "workspace" }],
      } as never),
    ).toThrow(/invalid.*text/);
  }
});
it("rejects incompatible bridges, invalid routes and duplicate local identities", () => {
  expect(() => validateContribution({ bridge: 2 } as never)).toThrow(
    /unsupported/,
  );
  for (const id of ["../agents", "overview/details", ""])
    expect(() =>
      validateContribution({
        bridge: 1,
        pages: [{ id, label: "Report", component }],
      }),
    ).toThrow(/Invalid/);
  expect(() =>
    validateContribution({
      bridge: 1,
      pages: [
        { id: "report", label: "Report", component },
        { id: "report", label: "Report", component },
      ],
    }),
  ).toThrow(/duplicate/);
  expect(() =>
    validateContribution({
      bridge: 1,
      settings: [
        { id: "report", scope: "workspace", label: "Report", component },
        { id: "report", scope: "workspace", label: "Report", component },
      ],
    }),
  ).toThrow(/duplicate/);
});

it("validates optional App navigation metadata", () => {
  expect(
    validateContribution({
      bridge: 1,
      navigation: { label: { en: "Reports", "zh-CN": "报告" }, icon: "robot" },
    }).navigation?.icon,
  ).toBe("robot");
  expect(() =>
    validateContribution({
      bridge: 1,
      navigation: { icon: "unknown" },
    } as never),
  ).toThrow("Unsupported App navigation icon");
  expect(() =>
    validateContribution({
      bridge: 1,
      navigation: { label: { en: 2 } },
    } as never),
  ).toThrow("Invalid App navigation label");
});
