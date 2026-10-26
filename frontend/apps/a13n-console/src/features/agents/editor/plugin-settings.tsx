import { PuzzlePieceIcon } from "@phosphor-icons/react";
import {
  Button,
  FormField,
  Input,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  CatalogStep,
  DirectoryEmpty,
  DirectoryList,
  DirectoryRow,
  type ResourceEditorControl,
} from "../../../shared/dialogs";
import { ErrorNotice } from "../../../shared/feedback";
import {
  jsonObject,
  SchemaFields,
  TextAreaField,
  withSchemaValues,
} from "../../../shared/forms";
import {
  validatePluginSettings,
  hasPluginForm,
  instanceNameError,
  suggestedInstanceName,
  type Plugin,
  type PluginType,
} from "../plugins";

export function PluginSettings({
  initial,
  plugins,
  catalog,
  readOnly,
  control,
  onApply,
}: {
  initial?: Plugin;
  plugins: Plugin[];
  catalog: PluginType[];
  readOnly: boolean;
  control: ResourceEditorControl;
  onApply: (plugin: Plugin) => void;
}) {
  const { t } = useTranslation();
  const formId = useId();
  const [selected, setSelected] = useState(initial?.plugin_key);
  const definition = catalog.find((item) => item.plugin_key === selected);
  const [query, setQuery] = useState("");
  const [name, setName] = useState(initial?.instance_name ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(
    initial?.config ?? {},
  );
  const [json, setJson] = useState(
    JSON.stringify(initial?.config ?? {}, null, 2),
  );
  const [mode, setMode] = useState(
    hasPluginForm(definition?.config_schema ?? null) ? "form" : "json",
  );
  const [error, setError] = useState<Error>();
  const [attempted, setAttempted] = useState(false);
  const nameError = instanceNameError(name, plugins, initial?.instance_name);
  const close = () => control.onClose?.();
  function select(item: PluginType) {
    const value =
      item.config_schema && hasPluginForm(item.config_schema)
        ? withSchemaValues(item.config_schema, {})
        : {};
    setSelected(item.plugin_key);
    setName(suggestedInstanceName(item.plugin_key, plugins));
    setConfig(value);
    setJson(JSON.stringify(value, null, 2));
    setMode(hasPluginForm(item.config_schema) ? "form" : "json");
    setError(undefined);
    setAttempted(false);
  }
  function changeMode(next: string) {
    try {
      if (next === "form") setConfig(jsonObject(json));
      else setJson(JSON.stringify(config, null, 2));
      setMode(next);
      setError(undefined);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause : new Error(t("Invalid configuration")),
      );
    }
  }
  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    setAttempted(true);
    if (nameError || !selected || readOnly) return;
    try {
      if (!event.currentTarget.reportValidity()) return;
      const value = jsonObject(mode === "json" ? json : JSON.stringify(config));
      if (definition?.config_schema)
        validatePluginSettings(definition.config_schema, value);
      onApply({ instance_name: name, plugin_key: selected, config: value });
      close();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause : new Error(t("Invalid configuration")),
      );
    }
  }
  const matches = catalog.filter((item) =>
    `${item.display_name} ${item.plugin_key} ${item.description} ${t(item.display_name)} ${t(item.description)}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  return (
    <ModalFrame
      open={control.controlledOpen}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      finalFocus={control.finalFocus}
      size="lg"
      title={selected ? t("Configure plugin") : t("Add plugin")}
      closeLabel={t("Close")}
      description={
        selected
          ? t(definition?.display_name ?? selected)
          : t("Choose a plugin enabled in this deployment.")
      }
      footer={
        <>
          <Button type="button" variant="outline" onClick={close}>
            {readOnly ? t("Close") : t("Cancel")}
          </Button>
          {selected && !readOnly && (
            <Button type="submit" form={formId}>
              {initial ? t("Apply to draft") : t("Add to draft")}
            </Button>
          )}
        </>
      }
    >
      {!selected ? (
        <DirectoryList
          search={
            <Input
              type="search"
              aria-label={t("Search plugins")}
              placeholder={t("Search name or plugin key…")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          }
        >
          {matches.map((item) => (
            <DirectoryRow
              key={item.plugin_key}
              icon={<PuzzlePieceIcon />}
              name={t(item.display_name)}
              detail={[t(item.description), item.plugin_key]
                .filter(Boolean)
                .join(" · ")}
              onClick={() => select(item)}
            />
          ))}
          {!matches.length && (
            <DirectoryEmpty>{t("No matching plugins.")}</DirectoryEmpty>
          )}
        </DirectoryList>
      ) : (
        <CatalogStep
          backLabel={t("Back to plugins")}
          onBack={!initial ? () => setSelected(undefined) : undefined}
        >
          <form id={formId} onSubmit={apply} noValidate className="grid gap-5">
            <p className="text-muted-foreground text-xs break-all">
              {selected}
              {definition?.distribution_version
                ? ` · ${definition.distribution_version}`
                : ""}
            </p>
            {definition?.description && (
              <p className="text-muted-foreground text-sm">
                {t(definition.description)}
              </p>
            )}
            <FormField
              label={t("Instance name")}
              error={attempted && nameError ? t(nameError) : undefined}
              description={t(
                "Unique within this agent. Use different names for multiple instances of the same plugin.",
              )}
            >
              <Input
                value={name}
                readOnly={readOnly}
                maxLength={128}
                autoFocus={!readOnly}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            {!readOnly && hasPluginForm(definition?.config_schema ?? null) && (
              <SegmentedControl
                label={t("Plugin configuration format")}
                value={mode}
                onValueChange={changeMode}
                options={[
                  { value: "form", label: t("Form") },
                  { value: "json", label: "JSON" },
                ]}
              />
            )}
            {!readOnly && mode === "form" && definition?.config_schema ? (
              <SchemaFields
                schema={definition.config_schema}
                value={config}
                onChange={(value) => {
                  setConfig(value);
                  setError(undefined);
                }}
              />
            ) : (
              <TextAreaField
                label={t("Plugin configuration (JSON)")}
                code
                rows={8}
                readOnly={readOnly}
                value={
                  readOnly
                    ? JSON.stringify(initial?.config ?? {}, null, 2)
                    : json
                }
                onChange={(value) => {
                  setJson(value);
                  setError(undefined);
                }}
              />
            )}
            <ErrorNotice error={error} />
            {!readOnly && (
              <p className="text-muted-foreground text-xs">
                {t(
                  "Changes stay in this draft until you save the agent. Saving validates configuration without running the plugin.",
                )}
              </p>
            )}
          </form>
        </CatalogStep>
      )}
    </ModalFrame>
  );
}
