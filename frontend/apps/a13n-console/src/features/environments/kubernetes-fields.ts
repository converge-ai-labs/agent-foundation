/** Form projections of native Kubernetes fields, not a replacement API schema. */
export type NativeObject = Record<string, unknown>;
export function object(value: unknown): NativeObject {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as NativeObject)
    : {};
}
export function objects(value: unknown): NativeObject[] {
  return Array.isArray(value) ? value.map(object) : [];
}
export function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}
export function textField(title: string, description?: string) {
  return { type: "string", title, ...(description ? { description } : {}) };
}
export function choiceField(title: string, choices: string[]) {
  return { type: "string", title, enum: choices };
}
export function listField(
  title: string,
  properties: NativeObject,
  required: string[] = [],
) {
  return {
    type: "array",
    title,
    items: { type: "object", properties, required },
  };
}
export function stringList(title: string) {
  return { type: "array", title, items: { type: "string", title: "Value" } };
}
export const containerFields = {
  name: textField("Container name"),
  image: textField(
    "Container image",
    "Use an image the cluster nodes can pull, or load your local build into the cluster.",
  ),
  imagePullPolicy: choiceField("Image pull policy", [
    "IfNotPresent",
    "Always",
    "Never",
  ]),
};
export const commandFields = {
  command: stringList("Command"),
  args: stringList("Arguments"),
};
export const resourceFields = {
  requests: {
    type: "object",
    title: "Resource requests",
    properties: {
      cpu: textField("CPU request", "For example, 500m or 2."),
      memory: textField("Memory request", "For example, 512Mi or 2Gi."),
      "ephemeral-storage": textField("Ephemeral storage request"),
    },
  },
  limits: {
    type: "object",
    title: "Resource limits",
    properties: {
      cpu: textField("CPU limit"),
      memory: textField("Memory limit"),
      "ephemeral-storage": textField("Ephemeral storage limit"),
      "nvidia.com/gpu": textField("NVIDIA GPU limit"),
    },
  },
};
export const mountFields = {
  volumeMounts: listField(
    "Native volume mounts",
    {
      name: textField("Volume name"),
      mountPath: textField("Mount path"),
      subPath: textField("Subpath (optional)"),
      readOnly: { type: "boolean", title: "Read only" },
    },
    ["name", "mountPath"],
  ),
};
export const schedulingFields = {
  restartPolicy: choiceField("Restart policy", [
    "Never",
    "OnFailure",
    "Always",
  ]),
  priorityClassName: textField("Priority class (optional)"),
  runtimeClassName: textField("Runtime class (optional)"),
  schedulerName: textField("Scheduler (optional)"),
  tolerations: listField("Tolerations", {
    key: textField("Taint key (optional)"),
    operator: choiceField("Operator", ["Equal", "Exists"]),
    value: textField("Taint value (optional)"),
    effect: choiceField("Effect", [
      "NoSchedule",
      "PreferNoSchedule",
      "NoExecute",
    ]),
    tolerationSeconds: {
      type: "integer",
      title: "Toleration seconds (optional)",
      minimum: 0,
    },
  }),
};
export const connectionFields = {
  working_directory: {
    ...textField("Working directory"),
    default: "/workspace",
  },
  envd_port: {
    type: "integer",
    title: "Envd port",
    default: 8888,
    minimum: 1,
    maximum: 65535,
  },
  request_timeout_seconds: {
    type: "number",
    title: "Request timeout (seconds)",
    default: 30,
    minimum: 0.001,
    maximum: 120,
  },
};
export const claimFields = {
  storageClassName: textField(
    "Storage class (optional)",
    "Leave unset to use the cluster default.",
  ),
  accessModes: {
    type: "array",
    title: "Access modes",
    items: choiceField("Access mode", [
      "ReadWriteOnce",
      "ReadWriteMany",
      "ReadOnlyMany",
      "ReadWriteOncePod",
    ]),
  },
  resources: {
    type: "object",
    properties: {
      requests: {
        type: "object",
        properties: {
          storage: textField("Storage size", "For example, 10Gi."),
        },
        required: ["storage"],
      },
    },
  },
};

/** Keep the execution binding attached when its container is renamed. */
export function updateContainers(
  recipe: NativeObject,
  next: NativeObject[],
): NativeObject {
  const template = object(recipe.pod_template),
    spec = object(template.spec);
  const previous = objects(spec.containers);
  const index = previous.findIndex(
    (item) => item.name === recipe.execution_container,
  );
  const renamed =
    next.length === previous.length &&
    index >= 0 &&
    next[index]?.name !== previous[index]?.name;
  return {
    ...recipe,
    ...(renamed ? { execution_container: next[index]?.name } : {}),
    pod_template: { ...template, spec: { ...spec, containers: next } },
  };
}

/** Source changes remove only mutually exclusive storage fields. */
export function storageSource(
  binding: NativeObject,
  source: string,
): NativeObject {
  const next = { ...binding };
  if (source === "existing") {
    delete next.claim_template;
    next.existing_claim = "";
    next.disposition = "retain";
  } else {
    delete next.existing_claim;
    delete next.shared_directory;
    next.claim_template = {
      spec: {
        accessModes: ["ReadWriteOnce"],
        resources: { requests: { storage: "10Gi" } },
      },
    };
  }
  return next;
}
