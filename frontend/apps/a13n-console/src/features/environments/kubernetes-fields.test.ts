import { expect, it } from "vitest";
import { storageSource, updateContainers } from "./kubernetes-fields";

it("tracks an execution container rename while retaining native fields", () => {
  const recipe = {
    execution_container: "worker",
    connection: "direct",
    pod_template: {
      metadata: { labels: { team: "ml" } },
      spec: {
        affinity: { nodeAffinity: {} },
        containers: [
          { name: "helper", image: "helper:1" },
          { name: "worker", image: "sandbox:1" },
        ],
      },
    },
  };
  const next = updateContainers(recipe, [
    { name: "helper", image: "helper:1" },
    { name: "renamed", image: "sandbox:1" },
  ]);
  expect(next).toEqual({
    ...recipe,
    execution_container: "renamed",
    pod_template: {
      ...recipe.pod_template,
      spec: {
        ...recipe.pod_template.spec,
        containers: [
          { name: "helper", image: "helper:1" },
          { name: "renamed", image: "sandbox:1" },
        ],
      },
    },
  });
  expect(recipe.execution_container).toBe("worker");
});

it.each([
  { names: ["helper"], selected: "" },
  { names: [], selected: "" },
  { names: ["worker"], selected: "worker" },
])(
  "updates the execution binding after removing containers: $names",
  ({ names, selected }) => {
    const recipe = {
      execution_container: "worker",
      pod_template: {
        metadata: { labels: { team: "ml" } },
        spec: {
          affinity: { nodeAffinity: {} },
          containers: [
            { name: "helper", image: "helper:1" },
            { name: "worker", image: "sandbox:1" },
          ],
        },
      },
    };
    const containers = recipe.pod_template.spec.containers.filter((item) =>
      names.includes(item.name),
    );
    expect(updateContainers(recipe, containers)).toEqual({
      ...recipe,
      execution_container: selected,
      pod_template: {
        ...recipe.pod_template,
        spec: { ...recipe.pod_template.spec, containers },
      },
    });
    expect(recipe.execution_container).toBe("worker");
    expect(recipe.pod_template.spec.containers).toHaveLength(2);
  },
);

it("switches storage sources without leaving conflicting native bindings", () => {
  const fresh = storageSource(
    {
      name: "workspace",
      mount_path: "/workspace",
      read_only: true,
      existing_claim: "shared",
      shared_directory: true,
    },
    "new",
  );
  expect(fresh.existing_claim).toBeUndefined();
  expect(fresh.shared_directory).toBeUndefined();
  expect(fresh.claim_template).toEqual({
    spec: {
      accessModes: ["ReadWriteOnce"],
      resources: { requests: { storage: "10Gi" } },
    },
  });
  expect(fresh.read_only).toBe(true);
  const existing = storageSource(
    { ...fresh, disposition: "delete" },
    "existing",
  );
  expect(existing.claim_template).toBeUndefined();
  expect(existing.disposition).toBe("retain");
  expect(existing.existing_claim).toBe("");
});
