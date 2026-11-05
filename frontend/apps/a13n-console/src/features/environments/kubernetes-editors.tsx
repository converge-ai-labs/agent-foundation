import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { SchemaFields } from "../../shared/forms";
import {
  claimFields,
  commandFields,
  containerFields,
  mountFields,
  object,
  objects,
  resourceFields,
  storageSource,
  string,
  textField,
  type NativeObject,
} from "./kubernetes-fields";
import styles from "./kubernetes.module.css";

export function Fields({
  properties,
  value,
  onChange,
  required = [],
  readOnly = false,
}: {
  properties: NativeObject;
  value: NativeObject;
  onChange: (next: NativeObject) => void;
  required?: string[];
  readOnly?: boolean;
}) {
  return (
    <fieldset disabled={readOnly} className={styles.fields}>
      <SchemaFields
        schema={{ properties, required }}
        value={value}
        onChange={onChange}
      />
    </fieldset>
  );
}

/** Row identities survive removal and edits, including nested unsaved controls. */
export function Items({
  value,
  onChange,
  addLabel,
  create,
  children,
  readOnly = false,
}: {
  value: NativeObject[];
  onChange: (next: NativeObject[]) => void;
  addLabel: string;
  create: () => NativeObject;
  readOnly?: boolean;
  children: (
    value: NativeObject,
    change: (next: NativeObject) => void,
    index: number,
  ) => ReactNode;
}) {
  const { t } = useTranslation();
  const serialized = JSON.stringify(value);
  const [source, setSource] = useState(serialized);
  const [rows, setRows] = useState(() =>
    value.map((value, id) => ({ value, id })),
  );
  const [nextId, setNextId] = useState(rows.length);
  if (source !== serialized) {
    setSource(serialized);
    setRows(value.map((value, id) => ({ value, id })));
    setNextId(value.length);
  }
  function update(next: typeof rows) {
    setRows(next);
    const values = next.map((row) => row.value);
    setSource(JSON.stringify(values));
    onChange(values);
  }
  return (
    <div className={styles.items}>
      {rows.map((row, index) => (
        <div className={styles.item} key={row.id}>
          <div className={styles.itemHeading}>
            <span>{string(row.value.name) || t("Item") + ` ${index + 1}`}</span>
            {!readOnly && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={t("Remove item") + ` ${index + 1}`}
                onClick={() =>
                  update(rows.filter((item) => item.id !== row.id))
                }
              >
                {t("Remove")}
              </Button>
            )}
          </div>
          {children(
            row.value,
            (value) =>
              update(
                rows.map((item) =>
                  item.id === row.id ? { ...item, value } : item,
                ),
              ),
            index,
          )}
        </div>
      ))}
      {!readOnly && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={styles.add}
          onClick={() => {
            update([...rows, { id: nextId, value: create() }]);
            setNextId(nextId + 1);
          }}
        >
          {addLabel}
        </Button>
      )}
    </div>
  );
}

export function MapFields({
  label,
  description,
  value,
  onChange,
  readOnly = false,
}: {
  label: string;
  description?: string;
  value: NativeObject;
  onChange: (next: NativeObject) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const serialized = JSON.stringify(value);
  const [source, setSource] = useState(serialized);
  const [rows, setRows] = useState(() =>
    Object.entries(value).map(([name, value], id) => ({
      id,
      name,
      value: string(value),
    })),
  );
  const [nextId, setNextId] = useState(rows.length);
  if (source !== serialized) {
    setSource(serialized);
    setRows(
      Object.entries(value).map(([name, value], id) => ({
        id,
        name,
        value: string(value),
      })),
    );
    setNextId(Object.keys(value).length);
  }
  function update(next: typeof rows) {
    setRows(next);
    // Keep incomplete rows local: object conversion would discard duplicates.
    const names = next.map(({ name }) => name);
    if (names.some((name) => !name) || new Set(names).size !== names.length) {
      return;
    }
    const mapping = Object.fromEntries(
      next.map(({ name, value }) => [name, value]),
    );
    setSource(JSON.stringify(mapping));
    onChange(mapping);
  }
  return (
    <fieldset className={styles.fields} disabled={readOnly}>
      <legend className={styles.legend}>{label}</legend>
      {description && <p className={styles.hint}>{description}</p>}
      {rows.map((row, index) => {
        const duplicate = rows.some(
          (other) => other.id !== row.id && other.name === row.name,
        );
        return (
          <div key={row.id} className={styles.pair}>
            <Input
              aria-label={`${label} ${t("key")} ${index + 1}`}
              placeholder={t("Key")}
              value={row.name}
              required
              ref={(input) =>
                input?.setCustomValidity(
                  duplicate ? t("Keys must be unique.") : "",
                )
              }
              onChange={(event) =>
                update(
                  rows.map((item) =>
                    item.id === row.id
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                )
              }
            />
            <Input
              aria-label={`${label} ${t("value")} ${index + 1}`}
              placeholder={t("Value")}
              value={row.value}
              onChange={(event) =>
                update(
                  rows.map((item) =>
                    item.id === row.id
                      ? { ...item, value: event.target.value }
                      : item,
                  ),
                )
              }
            />
            {!readOnly && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`${t("Remove")} ${label} ${index + 1}`}
                onClick={() =>
                  update(rows.filter((item) => item.id !== row.id))
                }
              >
                {t("Remove")}
              </Button>
            )}
            {duplicate && <p role="alert">{t("Keys must be unique.")}</p>}
          </div>
        );
      })}
      {!readOnly && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={styles.add}
          onClick={() => {
            update([...rows, { id: nextId, name: "", value: "" }]);
            setNextId(nextId + 1);
          }}
        >
          {t("Add entry")}
        </Button>
      )}
    </fieldset>
  );
}

function ContainerVariables({
  value,
  onChange,
  readOnly,
}: {
  value: NativeObject[];
  onChange: (next: NativeObject[]) => void;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Items
      value={value}
      onChange={onChange}
      readOnly={readOnly}
      addLabel={t("Add container variable")}
      create={() => ({ name: "", value: "" })}
    >
      {(entry, change) => {
        const from = object(entry.valueFrom);
        const source = Object.keys(from)[0] ?? "value";
        const reference = object(from[source]);
        return (
          <>
            <Fields
              properties={{ name: textField("Variable name") }}
              value={entry}
              onChange={change}
              required={["name"]}
              readOnly={readOnly}
            />
            <ChoiceField
              label={t("Value source")}
              value={source}
              disabled={readOnly}
              options={[
                { value: "value", label: t("Literal value") },
                { value: "secretKeyRef", label: t("Secret key") },
                { value: "configMapKeyRef", label: t("ConfigMap key") },
                { value: "fieldRef", label: t("Pod field") },
                { value: "resourceFieldRef", label: t("Container resource") },
              ]}
              onValueChange={(source) => {
                const next = { ...entry };
                delete next.value;
                delete next.valueFrom;
                if (source === "value") next.value = "";
                else next.valueFrom = { [source]: {} };
                change(next);
              }}
            />
            {source === "value" ? (
              <FormField label={t("Variable value")}>
                <Input
                  value={string(entry.value)}
                  readOnly={readOnly}
                  onChange={(event) =>
                    change({ ...entry, value: event.target.value })
                  }
                />
              </FormField>
            ) : (
              <Fields
                properties={
                  source === "fieldRef"
                    ? { fieldPath: textField("Field path") }
                    : source === "resourceFieldRef"
                      ? {
                          resource: textField("Resource"),
                          containerName: textField("Container name (optional)"),
                          divisor: textField("Divisor (optional)"),
                        }
                      : {
                          name: textField("Reference name"),
                          key: textField("Reference key"),
                          optional: {
                            type: "boolean",
                            title: "Optional reference",
                          },
                        }
                }
                required={
                  source === "fieldRef"
                    ? ["fieldPath"]
                    : source === "resourceFieldRef"
                      ? ["resource"]
                      : ["name", "key"]
                }
                value={reference}
                readOnly={readOnly}
                onChange={(next) =>
                  change({ ...entry, valueFrom: { ...from, [source]: next } })
                }
              />
            )}
          </>
        );
      }}
    </Items>
  );
}

export function Containers({
  value,
  onChange,
  readOnly = false,
  init = false,
}: {
  value: NativeObject[];
  onChange: (next: NativeObject[]) => void;
  readOnly?: boolean;
  init?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Items
      value={value}
      onChange={onChange}
      readOnly={readOnly}
      addLabel={t(init ? "Add init container" : "Add container")}
      create={() => ({ name: "", image: "" })}
    >
      {(container, change) => (
        <>
          <Fields
            properties={containerFields}
            value={container}
            onChange={change}
            required={["name", "image"]}
            readOnly={readOnly}
          />
          <DisclosureSection title={t("Resources and startup")}>
            <Fields
              properties={resourceFields}
              value={object(container.resources)}
              readOnly={readOnly}
              onChange={(resources) => change({ ...container, resources })}
            />
            <Fields
              properties={commandFields}
              value={container}
              onChange={change}
              readOnly={readOnly}
            />
          </DisclosureSection>
          <DisclosureSection title={t("Container variables and mounts")}>
            <p className={styles.hint}>
              {t(
                "Use Secret or ConfigMap references for sensitive values. A13N_ENVD_ variables are managed by the Service.",
              )}
            </p>
            <ContainerVariables
              value={objects(container.env)}
              readOnly={readOnly}
              onChange={(env) => change({ ...container, env })}
            />
            <Items
              value={objects(container.envFrom)}
              readOnly={readOnly}
              addLabel={t("Import variables")}
              create={() => ({ configMapRef: { name: "" } })}
              onChange={(envFrom) => change({ ...container, envFrom })}
            >
              {(entry, update) => {
                const source = entry.secretRef ? "secretRef" : "configMapRef";
                return (
                  <>
                    <ChoiceField
                      label={t("Import from")}
                      value={source}
                      disabled={readOnly}
                      options={[
                        { value: "configMapRef", label: "ConfigMap" },
                        { value: "secretRef", label: "Secret" },
                      ]}
                      onValueChange={(next) => {
                        const value = { ...entry };
                        delete value[source];
                        value[next] = { name: "" };
                        update(value);
                      }}
                    />
                    <Fields
                      properties={{
                        name: textField("Reference name"),
                        optional: {
                          type: "boolean",
                          title: "Optional reference",
                        },
                      }}
                      value={object(entry[source])}
                      required={["name"]}
                      readOnly={readOnly}
                      onChange={(next) => update({ ...entry, [source]: next })}
                    />
                    <Fields
                      properties={{
                        prefix: textField("Variable prefix (optional)"),
                      }}
                      value={entry}
                      onChange={update}
                      readOnly={readOnly}
                    />
                  </>
                );
              }}
            </Items>
            <Fields
              properties={mountFields}
              value={container}
              onChange={change}
              readOnly={readOnly}
            />
          </DisclosureSection>
        </>
      )}
    </Items>
  );
}

export function Storage({
  value,
  onChange,
  readOnly = false,
}: {
  value: NativeObject[];
  onChange: (next: NativeObject[]) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Items
      value={value}
      onChange={onChange}
      readOnly={readOnly}
      addLabel={t("Add storage")}
      create={() =>
        storageSource(
          { name: "", mount_path: "/workspace", disposition: "retain" },
          "new",
        )
      }
    >
      {(binding, change) => {
        const existing = typeof binding.existing_claim === "string";
        const claim = object(binding.claim_template);
        return (
          <>
            <Fields
              properties={{
                name: textField("Storage name"),
                mount_path: textField("Mount path"),
              }}
              value={binding}
              onChange={change}
              required={["name", "mount_path"]}
              readOnly={readOnly}
            />
            <ChoiceField
              label={t("Storage source")}
              value={existing ? "existing" : "new"}
              disabled={readOnly}
              options={[
                { value: "new", label: t("New persistent volume claim") },
                {
                  value: "existing",
                  label: t("Existing persistent volume claim"),
                },
              ]}
              onValueChange={(source) => change(storageSource(binding, source))}
            />
            {existing ? (
              <>
                <Fields
                  properties={{
                    existing_claim: textField("Existing claim name"),
                    shared_directory: {
                      type: "boolean",
                      title: "Separate directory per instance",
                      description:
                        "Create an instance subdirectory on the borrowed claim. The directory is retained after deletion.",
                    },
                  }}
                  value={binding}
                  onChange={change}
                  required={["existing_claim"]}
                  readOnly={readOnly}
                />
                <p className={styles.hint}>
                  {t(
                    "Existing claims are borrowed and are never deleted by the Service.",
                  )}
                </p>
              </>
            ) : (
              <>
                <Fields
                  properties={claimFields}
                  value={object(claim.spec)}
                  readOnly={readOnly}
                  onChange={(spec) =>
                    change({ ...binding, claim_template: { ...claim, spec } })
                  }
                />
                <ChoiceField
                  label={t("When the instance is deleted")}
                  value={string(binding.disposition) || "retain"}
                  disabled={readOnly}
                  options={[
                    { value: "retain", label: t("Retain the claim and data") },
                    { value: "delete", label: t("Delete the claim") },
                  ]}
                  onValueChange={(disposition) =>
                    change({ ...binding, disposition })
                  }
                />
                {binding.disposition === "delete" && (
                  <p className={styles.hint}>
                    {t(
                      "Deleting the instance will delete this claim. The storage class determines whether the underlying data is also removed.",
                    )}
                  </p>
                )}
              </>
            )}
            <Fields
              properties={{
                read_only: { type: "boolean", title: "Read only" },
              }}
              value={binding}
              onChange={change}
              readOnly={readOnly}
            />
          </>
        );
      }}
    </Items>
  );
}
