import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { MetadataChips, metadataChips } from "./metadata";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("uses only the observation's explicit metadata namespace and resource environment", () => {
  const observation = {
    attributes: {
      "deployment.environment.name": "wrong-scope",
      "a13n.observation.labels": ["fast", "trial"],
      "a13n.observation.metadata.region": "west",
      "a13n.observation.metadata.enabled": false,
      "a13n.observation.metadata.nested": { inspect: "in attributes" },
      "a13n.observation.metadata.organization_id": "org_correlation",
      "a13n.observation.metadata.service_run_id": "run_correlation",
      "a13n.observation.metadata": { ignored: true },
      "user.email": "not-a-chip@example.com",
    },
    resource_attributes: { "deployment.environment.name": "development" },
  } as unknown as Schema["Span"];
  expect(metadataChips(observation).map(({ value }) => value)).toEqual([
    "development",
    "fast",
    "trial",
    "west",
    "false",
  ]);
  render(<MetadataChips observation={observation} />);
  expect(screen.getByText("+1")).toBeTruthy();
  expect(screen.queryByText("false")).toBeNull();
  expect(screen.queryByText("wrong-scope")).toBeNull();
  expect(
    metadataChips({ ...observation, attributes: {}, resource_attributes: {} }),
  ).toEqual([]);
});
