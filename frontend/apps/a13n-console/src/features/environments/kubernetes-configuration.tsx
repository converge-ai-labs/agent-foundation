import { ConfigurationGroup } from "./configuration-group";
import { EnvdSettings } from "./envd-settings";
import {
  Button,
  ChoiceField,
  DisclosureSection,
  SegmentedControl,
} from "a13n-ui";
import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FormSection, jsonObject, TextAreaField } from "../../shared/forms";
import {
  Containers,
  Fields,
  Items,
  MapFields,
  Storage,
} from "./kubernetes-editors";
import {
  connectionFields,
  listField,
  object,
  objects,
  schedulingFields,
  string,
  textField,
  updateContainers,
  type NativeObject,
} from "./kubernetes-fields";
import styles from "./kubernetes.module.css";

/** Forms patch the native recipe in place; undisplayed fields stay intact. */
export function KubernetesConfiguration({
  schema,
  text,
  onChange,
  readOnly = false,
  error,
  children,
}: {
  schema?: NativeObject;
  text: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error?: string;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const [advanced, setAdvanced] = useState(false);
  let recipe: NativeObject | undefined;
  try {
    recipe = jsonObject(text);
  } catch {
    /* Keep unfinished native input editable. */
  }
  const empty = recipe && Object.keys(recipe).length === 0;
  const presets = object(schema?.["x-environment-presets"]);
  const template = object(recipe?.pod_template),
    spec = object(template.spec);
  const metadata = object(template.metadata);
  const containers = objects(spec.containers);
  function change(next: NativeObject) {
    onChange(JSON.stringify(next, null, 2));
  }
  function changeSpec(next: NativeObject) {
    change({ ...recipe, pod_template: { ...template, spec: next } });
  }
  const connectionOptions = [
    { value: "direct", label: t("Direct Pod connection") },
    { value: "port_forward", label: t("Local port forwarding") },
  ];
  return (
    <FormSection
      title={t("Configuration")}
      actions={
        <SegmentedControl
          label={t("Configuration editor")}
          value={advanced || !recipe ? "json" : "fields"}
          onValueChange={(mode) => setAdvanced(mode === "json")}
          options={[
            { value: "fields", label: t("Fields"), disabled: !recipe },
            { value: "json", label: "JSON" },
          ]}
        />
      }
    >
      {advanced || !recipe ? (
        <TextAreaField
          label={t("Native recipe JSON")}
          value={text}
          onChange={onChange}
          readOnly={readOnly}
          code
          rows={18}
        />
      ) : (
        <>
          <ConfigurationGroup title={t("Provider platform settings")}>
            <ConfigurationGroup
              nested
              title={t("Sandbox connection")}
              description={t(
                "Cluster credentials and the sandbox ServiceAccount are managed by the operator. Choose how the Service reaches Envd.",
              )}
            >
              {empty ? (
                <>
                  <p className={styles.hint}>
                    {t(
                      "Choose a connection to start configuring your sandbox.",
                    )}
                  </p>
                  <div className={styles.starters}>
                    {connectionOptions.map(({ value, label }) => (
                      <Button
                        key={value}
                        type="button"
                        variant="outline"
                        disabled={readOnly || !presets[value]}
                        onClick={() => change(object(presets[value]))}
                      >
                        {label}
                      </Button>
                    ))}
                  </div>
                </>
              ) : (
                recipe && (
                  <>
                    <ChoiceField
                      label={t("Connection mode")}
                      value={string(recipe.connection) || "direct"}
                      disabled={readOnly}
                      options={connectionOptions}
                      onValueChange={(connection) =>
                        change({ ...recipe, connection })
                      }
                    />
                    <p className={styles.hint}>
                      {t(
                        recipe.connection === "port_forward"
                          ? "For a Service outside the cluster. Requires an operator kubeconfig profile, kubectl, and allow_port_forward on both Control and Worker."
                          : "Works inside or outside the cluster when both Control and Worker can reach Pod IPs. Kubernetes API authentication comes from the operator profile.",
                      )}
                    </p>
                    <ChoiceField
                      label={t("Execution container")}
                      value={string(recipe.execution_container)}
                      disabled={readOnly}
                      required
                      options={containers
                        .filter((item) => string(item.name))
                        .map((item) => ({
                          value: string(item.name),
                          label: string(item.name),
                        }))}
                      onValueChange={(execution_container) =>
                        change({ ...recipe, execution_container })
                      }
                    />
                    <DisclosureSection title={t("Execution settings")}>
                      <Fields
                        properties={{
                          working_directory: connectionFields.working_directory,
                          envd_port: connectionFields.envd_port,
                        }}
                        value={recipe}
                        onChange={change}
                        readOnly={readOnly}
                      />
                    </DisclosureSection>
                    <MapFields
                      label={t("Execution variables")}
                      description={t(
                        "Defaults for commands and child processes. Values are ordinary configuration, not secrets.",
                      )}
                      value={object(recipe.environment)}
                      onChange={(environment) =>
                        change({ ...recipe, environment })
                      }
                      readOnly={readOnly}
                    />
                  </>
                )
              )}
              <p className={styles.hint}>
                {t(
                  "Both connection modes require the operator's allow_plaintext_envd opt-in. Build and load the sandbox image into the cluster, or use a registry image the nodes can pull.",
                )}
              </p>
              <a
                href="https://a13n.dev/docs/a13n-service/environments#kubernetes-sandboxes"
                target="_blank"
                rel="noreferrer"
                className={styles.guide}
              >
                {t("Kubernetes setup guide")}
                <ArrowSquareOutIcon size={14} />
              </a>
            </ConfigurationGroup>
            {recipe && !empty && (
              <>
                <ConfigurationGroup
                  nested
                  title={t("Containers")}
                  description={t(
                    "The execution container runs Envd. Add sidecars here; configure startup, resources and variables per container.",
                  )}
                >
                  <Containers
                    value={containers}
                    onChange={(next) => change(updateContainers(recipe, next))}
                    readOnly={readOnly}
                  />
                  <DisclosureSection title={t("Init containers")}>
                    <Containers
                      init
                      value={objects(spec.initContainers)}
                      onChange={(initContainers) =>
                        changeSpec({ ...spec, initContainers })
                      }
                      readOnly={readOnly}
                    />
                  </DisclosureSection>
                  <DisclosureSection title={t("Image pull secrets")}>
                    <Fields
                      properties={{
                        imagePullSecrets: listField(
                          "Image pull secrets",
                          { name: textField("Secret name") },
                          ["name"],
                        ),
                      }}
                      value={spec}
                      onChange={changeSpec}
                      readOnly={readOnly}
                    />
                  </DisclosureSection>
                </ConfigurationGroup>
                <ConfigurationGroup
                  nested
                  title={t("Persistent storage")}
                  description={t(
                    "Mount a new or existing claim into the execution container. New claims are retained by default.",
                  )}
                >
                  <Storage
                    value={objects(recipe.storage)}
                    onChange={(storage) => change({ ...recipe, storage })}
                    readOnly={readOnly}
                  />
                  <DisclosureSection title={t("Native volumes")}>
                    <p className={styles.hint}>
                      {t(
                        "Use native volumes for temporary files, ConfigMaps and Secrets. Add matching native volume mounts to each container that needs access.",
                      )}
                    </p>
                    <Items
                      value={objects(spec.volumes)}
                      onChange={(volumes) => changeSpec({ ...spec, volumes })}
                      readOnly={readOnly}
                      addLabel={t("Add volume")}
                      create={() => ({ name: "", emptyDir: {} })}
                    >
                      {(volume, update) => {
                        const kinds = [
                          "emptyDir",
                          "configMap",
                          "secret",
                          "persistentVolumeClaim",
                        ];
                        const kind =
                          kinds.find((key) => volume[key] != null) ?? "custom";
                        return (
                          <>
                            <Fields
                              properties={{ name: textField("Volume name") }}
                              value={volume}
                              onChange={update}
                              required={["name"]}
                              readOnly={readOnly}
                            />
                            <ChoiceField
                              label={t("Volume source")}
                              value={kind}
                              disabled={readOnly || kind === "custom"}
                              options={[
                                {
                                  value: "emptyDir",
                                  label: t("Temporary directory"),
                                },
                                { value: "configMap", label: "ConfigMap" },
                                { value: "secret", label: "Secret" },
                                {
                                  value: "persistentVolumeClaim",
                                  label: t("Existing claim"),
                                },
                                ...(kind === "custom"
                                  ? [
                                      {
                                        value: "custom",
                                        label: t(
                                          "Custom native volume (preserved)",
                                        ),
                                      },
                                    ]
                                  : []),
                              ]}
                              onValueChange={(next) => {
                                const value = { ...volume };
                                delete value[kind];
                                value[next] = {};
                                update(value);
                              }}
                            />
                            {kind !== "custom" && (
                              <Fields
                                properties={
                                  kind === "emptyDir"
                                    ? {
                                        medium: textField(
                                          "Medium (optional)",
                                          "Use Memory for a memory-backed volume.",
                                        ),
                                        sizeLimit: textField(
                                          "Size limit (optional)",
                                        ),
                                      }
                                    : kind === "secret"
                                      ? { secretName: textField("Secret name") }
                                      : kind === "configMap"
                                        ? { name: textField("ConfigMap name") }
                                        : { claimName: textField("Claim name") }
                                }
                                required={
                                  kind === "emptyDir"
                                    ? []
                                    : kind === "secret"
                                      ? ["secretName"]
                                      : kind === "configMap"
                                        ? ["name"]
                                        : ["claimName"]
                                }
                                value={object(volume[kind])}
                                onChange={(next) =>
                                  update({ ...volume, [kind]: next })
                                }
                                readOnly={readOnly}
                              />
                            )}
                          </>
                        );
                      }}
                    </Items>
                  </DisclosureSection>
                </ConfigurationGroup>
                <ConfigurationGroup nested title={t("Scheduling and metadata")}>
                  <DisclosureSection title={t("Placement and restart policy")}>
                    <Fields
                      properties={schedulingFields}
                      value={spec}
                      onChange={changeSpec}
                      readOnly={readOnly}
                    />
                  </DisclosureSection>
                  <MapFields
                    label={t("Node selector")}
                    value={object(spec.nodeSelector)}
                    onChange={(nodeSelector) =>
                      changeSpec({ ...spec, nodeSelector })
                    }
                    readOnly={readOnly}
                  />
                  {(["labels", "annotations"] as const).map((key) => (
                    <MapFields
                      key={key}
                      label={t(
                        key === "labels" ? "Pod labels" : "Pod annotations",
                      )}
                      value={object(metadata[key])}
                      readOnly={readOnly}
                      onChange={(next) =>
                        change({
                          ...recipe,
                          pod_template: {
                            ...template,
                            metadata: { ...metadata, [key]: next },
                          },
                        })
                      }
                    />
                  ))}
                </ConfigurationGroup>
              </>
            )}
          </ConfigurationGroup>
          <ConfigurationGroup title={t("Runtime settings")}>
            {recipe && !empty && (
              <DisclosureSection
                title={t("Advanced environment configuration")}
              >
                <Fields
                  properties={{
                    request_timeout_seconds:
                      connectionFields.request_timeout_seconds,
                  }}
                  value={recipe}
                  onChange={change}
                  readOnly={readOnly}
                />
              </DisclosureSection>
            )}
            {children}
          </ConfigurationGroup>
          {schema && recipe && !empty && (
            <EnvdSettings
              schema={schema}
              recipe={recipe}
              onChange={change}
              readOnly={readOnly}
            />
          )}
        </>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {(advanced || !recipe) && children}
    </FormSection>
  );
}
