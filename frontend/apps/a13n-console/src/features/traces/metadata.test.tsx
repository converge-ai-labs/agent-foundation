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
      "a13n.run_attempt.number": 2,
      "a13n.observation.labels": ["fast", "trial"],
      "a13n.observation.metadata.region": "west",
      "a13n.observation.metadata.enabled": false,
      "a13n.observation.metadata.nested": { inspect: "in attributes" },
      "a13n.observation.metadata": { ignored: true },
      "user.email": "not-a-chip@example.com",
    },
    resource_attributes: { "deployment.environment.name": "development" },
  } as unknown as Schema["Observation"];
  expect(metadataChips(observation).map(({ value }) => value)).toEqual([
    "development",
    "2",
    "fast",
    "trial",
    "west",
    "false",
  ]);
  render(<MetadataChips observation={observation} />);
  expect(screen.getByText("+2")).toBeTruthy();
  expect(screen.queryByText("west")).toBeNull();
  expect(screen.queryByText("wrong-scope")).toBeNull();
  expect(
    metadataChips({
      ...observation,
      attributes: null,
      resource_attributes: null,
    }),
  ).toEqual([]);
});
