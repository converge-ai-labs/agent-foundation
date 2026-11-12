import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { KubernetesConfiguration } from "./kubernetes-configuration";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const direct = {
  execution_container: "envd",
  connection: "direct",
  pod_template: {
    spec: {
      containers: [{ name: "envd", image: "sandbox:test" }],
      restartPolicy: "Never",
    },
  },
};
const schema = {
  "x-environment-presets": {
    direct,
    port_forward: { ...direct, connection: "port_forward" },
  },
};
function Editor({
  initial = "{}",
  readOnly = false,
}: {
  initial?: string;
  readOnly?: boolean;
}) {
  const [text, setText] = useState(initial);
  return (
    <>
      <KubernetesConfiguration
        schema={schema}
        text={text}
        onChange={setText}
        readOnly={readOnly}
      />
      <output data-testid="recipe">{text}</output>
    </>
  );
}
function recipe() {
  return JSON.parse(screen.getByTestId("recipe").textContent!);
}

it("builds a port-forward sandbox with resource and persistent storage forms, without JSON", async () => {
  render(<Editor />);
  const user = userEvent.setup();
  expect(
    screen.queryByRole("textbox", { name: "Native recipe JSON" }),
  ).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Local port forwarding" }),
  );
  const image = screen.getByRole("textbox", { name: "Container image" });
  fireEvent.change(image, { target: { value: "registry.example/sandbox:1" } });
  await user.click(
    screen.getByRole("button", { name: "Resources and startup" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "CPU request" }), {
    target: { value: "500m" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Memory limit" }), {
    target: { value: "2Gi" },
  });
  await user.click(screen.getByRole("button", { name: "Add storage" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Storage name" }), {
    target: { value: "workspace" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Storage size" }), {
    target: { value: "20Gi" },
  });
  expect(recipe()).toMatchObject({
    connection: "port_forward",
    execution_container: "envd",
    pod_template: {
      spec: {
        containers: [
          {
            name: "envd",
            image: "registry.example/sandbox:1",
            resources: { requests: { cpu: "500m" }, limits: { memory: "2Gi" } },
          },
        ],
      },
    },
    storage: [
      {
        name: "workspace",
        mount_path: "/workspace",
        disposition: "retain",
        claim_template: {
          spec: {
            accessModes: ["ReadWriteOnce"],
            resources: { requests: { storage: "20Gi" } },
          },
        },
      },
    ],
  });
  expect(
    screen.queryByRole("textbox", { name: "Native recipe JSON" }),
  ).toBeNull();
});

it("requires an explicit execution selection after removing its container", async () => {
  const initial = {
    ...direct,
    pod_template: {
      spec: {
        ...direct.pod_template.spec,
        containers: [
          direct.pod_template.spec.containers[0],
          { name: "helper", image: "sandbox:test" },
        ],
      },
    },
  };
  render(<Editor initial={JSON.stringify(initial)} />);
  const user = userEvent.setup();
  const containers = within(screen.getByRole("region", { name: "Containers" }));
  await user.click(containers.getByRole("button", { name: "Remove item 1" }));
  expect(recipe().execution_container).toBe("");
  expect(recipe().pod_template.spec.containers).toEqual([
    initial.pod_template.spec.containers[1],
  ]);
  const selection = screen.getByRole("combobox", {
    name: "Execution container",
  });
  expect(selection.textContent).not.toContain("envd");
  await user.click(selection);
  await user.click(await screen.findByRole("option", { name: "helper" }));
  expect(recipe().execution_container).toBe("helper");
});

it("reopens native recipes losslessly and keeps advanced drafts synchronized", async () => {
  const initial = {
    ...direct,
    network_policy: { spec: { policyTypes: ["Ingress"], ingress: [] } },
    pod_template: {
      metadata: { annotations: { "custom.example/owner": "team" } },
      spec: {
        ...direct.pod_template.spec,
        affinity: { nodeAffinity: {} },
        containers: [
          {
            ...direct.pod_template.spec.containers[0],
            securityContext: { runAsUser: 1000 },
            resources: { limits: { "example.com/device": "1" } },
          },
          {
            name: "sidecar",
            image: "sidecar:1",
            command: ["sleep", "infinity"],
          },
        ],
      },
    },
  };
  const { unmount } = render(<Editor initial={JSON.stringify(initial)} />);
  const user = userEvent.setup();
  fireEvent.change(
    screen.getAllByRole("textbox", { name: "Container image" })[0],
    { target: { value: "sandbox:2" } },
  );
  await user.click(
    screen.getAllByRole("button", { name: "Resources and startup" })[0],
  );
  fireEvent.change(screen.getByRole("textbox", { name: "CPU limit" }), {
    target: { value: "2" },
  });
  expect(recipe()).toEqual({
    ...initial,
    pod_template: {
      ...initial.pod_template,
      spec: {
        ...initial.pod_template.spec,
        containers: [
          {
            ...initial.pod_template.spec.containers[0],
            image: "sandbox:2",
            resources: { limits: { "example.com/device": "1", cpu: "2" } },
          },
          initial.pod_template.spec.containers[1],
        ],
      },
    },
  });
  const saved = JSON.stringify(recipe());
  unmount();
  render(<Editor initial={saved} />);
  expect(
    (
      screen.getAllByRole("textbox", {
        name: "Container image",
      })[0] as HTMLInputElement
    ).value,
  ).toBe("sandbox:2");
  await user.click(screen.getByRole("button", { name: "JSON" }));
  const raw = screen.getByRole("textbox", { name: "Native recipe JSON" });
  fireEvent.change(raw, { target: { value: "{" } });
  expect((raw as HTMLTextAreaElement).value).toBe("{");
  expect(screen.queryByRole("textbox", { name: "Container image" })).toBeNull();
  fireEvent.change(raw, { target: { value: saved } });
  expect(recipe()).toEqual(JSON.parse(saved));
});

it("edits Secret references and borrowed storage with explicit source controls", async () => {
  render(<Editor initial={JSON.stringify(direct)} />);
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Container variables and mounts" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Add container variable" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Variable name" }), {
    target: { value: "API_KEY" },
  });
  await user.click(screen.getByRole("combobox", { name: "Value source" }));
  await user.click(await screen.findByRole("option", { name: "Secret key" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Reference name" }), {
    target: { value: "app-secrets" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Reference key" }), {
    target: { value: "api-key" },
  });
  await user.click(screen.getByRole("button", { name: "Add storage" }));
  await user.click(screen.getByRole("combobox", { name: "Storage source" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Existing persistent volume claim",
    }),
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "Existing claim name" }),
    { target: { value: "team-workspace" } },
  );
  expect(recipe().pod_template.spec.containers[0].env).toEqual([
    {
      name: "API_KEY",
      valueFrom: { secretKeyRef: { name: "app-secrets", key: "api-key" } },
    },
  ]);
  expect(recipe().storage[0].existing_claim).toBe("team-workspace");
  expect(recipe().storage[0].claim_template).toBeUndefined();
  expect(
    screen.queryByRole("combobox", { name: "When the instance is deleted" }),
  ).toBeNull();
});

it.each([
  ["ConfigMap", "ConfigMap name", "configMap", "name"],
  ["Secret", "Secret name", "secret", "secretName"],
  ["Existing claim", "Claim name", "persistentVolumeClaim", "claimName"],
])(
  "requires the reference name for a native %s volume",
  async (source, label, kind, field) => {
    const { container } = render(
      <form>
        <Editor initial={JSON.stringify(direct)} />
      </form>,
    );
    const form = container.querySelector("form")!;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Native volumes" }));
    await user.click(screen.getByRole("button", { name: "Add volume" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Volume name" }), {
      target: { value: "data" },
    });
    expect(form.checkValidity()).toBe(true);
    await user.click(screen.getByRole("combobox", { name: "Volume source" }));
    await user.click(await screen.findByRole("option", { name: source }));
    expect(form.checkValidity()).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: label }), {
      target: { value: "existing-resource" },
    });
    expect(form.checkValidity()).toBe(true);
    expect(recipe().pod_template.spec.volumes).toEqual([
      { name: "data", [kind]: { [field]: "existing-resource" } },
    ]);
    fireEvent.change(screen.getByRole("textbox", { name: label }), {
      target: { value: "" },
    });
    expect(form.checkValidity()).toBe(false);
    await user.click(screen.getByRole("combobox", { name: "Volume source" }));
    await user.click(
      await screen.findByRole("option", { name: "Temporary directory" }),
    );
    expect(form.checkValidity()).toBe(true);
    expect(recipe().pod_template.spec.volumes).toEqual([
      { name: "data", emptyDir: {} },
    ]);
  },
);

it("keeps nested row drafts attached after removal and makes read-only controls inert", async () => {
  const initial = {
    ...direct,
    storage: [
      { name: "first", mount_path: "/first", existing_claim: "first-claim" },
      { name: "second", mount_path: "/second", existing_claim: "second-claim" },
    ],
  };
  const { unmount } = render(<Editor initial={JSON.stringify(initial)} />);
  const user = userEvent.setup();
  const storage = within(
    screen.getByRole("region", { name: "Persistent storage" }),
  );
  await user.click(storage.getByRole("button", { name: "Remove item 1" }));
  expect(
    (screen.getByRole("textbox", { name: "Storage name" }) as HTMLInputElement)
      .value,
  ).toBe("second");
  expect(recipe().storage).toEqual([initial.storage[1]]);
  unmount();
  render(<Editor initial={JSON.stringify(initial)} readOnly />);
  expect(screen.queryByRole("button", { name: "Add storage" })).toBeNull();
  expect(
    (
      screen.getByRole("combobox", {
        name: "Connection mode",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    screen.getByRole("textbox", { name: "Container image" }).closest("fieldset")
      ?.disabled,
  ).toBe(true);
});

it("keeps invalid map drafts visible and blocks submission without losing existing values", async () => {
  const initial = {
    ...direct,
    environment: { A: "first", B: "second" },
    pod_template: {
      ...direct.pod_template,
      metadata: {
        labels: { A: "first", B: "second" },
        annotations: { A: "first", B: "second" },
      },
      spec: {
        ...direct.pod_template.spec,
        nodeSelector: { A: "first", B: "second" },
      },
    },
  };
  const { container } = render(
    <form>
      <Editor initial={JSON.stringify(initial)} />
    </form>,
  );
  const form = container.querySelector("form")!;
  const user = userEvent.setup();
  for (const label of [
    "Execution variables",
    "Node selector",
    "Pod labels",
    "Pod annotations",
  ]) {
    const key = screen.getByRole("textbox", { name: `${label} key 2` });
    fireEvent.change(key, { target: { value: "A" } });
    expect(form.checkValidity()).toBe(false);
    expect(recipe()).toEqual(initial);
    for (const title of [
      "Execution settings",
      "Placement and restart policy",
    ]) {
      await user.click(screen.getByRole("button", { name: title }));
      await user.click(screen.getByRole("button", { name: title }));
    }
    expect(screen.getByRole("textbox", { name: `${label} key 2` })).toBe(key);
    expect(form.checkValidity()).toBe(false);
    fireEvent.change(key, { target: { value: "B" } });
    expect(form.checkValidity()).toBe(true);
    expect(recipe()).toEqual(initial);
  }
  await user.click(
    within(
      screen.getByRole("group", { name: "Execution variables" }),
    ).getByRole("button", { name: "Add entry" }),
  );
  expect(form.checkValidity()).toBe(false);
  expect(recipe()).toEqual(initial);
  fireEvent.change(
    screen.getByRole("textbox", { name: "Execution variables key 3" }),
    { target: { value: "C" } },
  );
  expect(form.checkValidity()).toBe(true);
  expect(recipe().environment).toEqual({ A: "first", B: "second", C: "" });
});
