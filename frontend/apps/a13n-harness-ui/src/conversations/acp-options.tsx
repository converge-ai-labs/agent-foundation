import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "../i18n";
import {
  acpModelIdentity,
  ModelIcon,
  ModelIdentity,
} from "../shell/model-identity";
import { ErrorNotice } from "../shell/ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import {
  SettingsChoices,
  SettingsRow,
  useComposerSettings,
} from "./composer-settings";
import panelStyles from "./composer-settings.module.css";

export function useAcpOptions(
  threadId: string,
  enabled: boolean,
  active: boolean,
  agentId?: string,
  continuation?: string | null,
) {
  const { client } = useTransport();
  return useQuery({
    queryKey: [
      "thread",
      threadId,
      "acp-options",
      agentId,
      continuation,
    ] as const,
    enabled: enabled && !active,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/acp-options", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}

export function acpOptionLabel(option?: Schema<"AcpOption">) {
  return option
    ? (option.choices?.find((choice) => choice.value === option.value)?.name ??
        String(option.value))
    : undefined;
}

export function AcpOptions({
  threadId,
  active,
  agentId,
  preset,
  continuation,
  options,
}: {
  threadId: string;
  active: boolean;
  agentId?: string;
  preset?: string | null;
  continuation?: string | null;
  options: ReturnType<typeof useAcpOptions>;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const key = [
    "thread",
    threadId,
    "acp-options",
    agentId,
    continuation,
  ] as const;
  const params = { path: { thread_id: threadId } };
  const select = useMutation({
    mutationFn: (body: Schema<"AcpOptionChange">) =>
      result(
        transport.client.PUT("/api/threads/{thread_id}/acp-options", {
          params,
          body,
        }),
      ),
    onSuccess: (updated) => {
      queries.setQueryData(key, updated);
    },
  });
  return (
    <AcpOptionFields
      options={options.data}
      preset={preset}
      loading={options.isFetching}
      disabled={active || select.isPending}
      error={select.error ?? options.error}
      onChange={select.mutate}
    />
  );
}

export function AcpOptionFields({
  options,
  preset,
  loading,
  disabled: locked,
  error,
  onChange,
}: {
  options?: Schema<"AcpOption">[];
  preset?: string | null;
  loading: boolean;
  disabled: boolean;
  error: unknown;
  onChange: (change: Schema<"AcpOptionChange">) => void;
}) {
  const { t } = useTranslation();
  const settings = useComposerSettings();
  const [fallbackExpanded, setFallbackExpanded] = useState<string>();
  const isModelOption = (option: Schema<"AcpOption">) =>
    option.category === "model" || option.id === "model";
  const optionChoices = (option: Schema<"AcpOption">) => {
    if (typeof option.value === "boolean")
      return [
        { label: t("Enabled"), value: "true" },
        { label: t("Disabled"), value: "false" },
      ];
    const model = isModelOption(option);
    return (option.choices ?? []).map(({ name, value }) => ({
      label: name,
      value,
      icon: model ? (
        <ModelIcon
          name={name}
          modelIdentity={acpModelIdentity(name, value, preset)}
        />
      ) : undefined,
    }));
  };
  const activeOption = settings?.page.startsWith("acp:")
    ? options?.find((option) => `acp:${option.id}` === settings.page)
    : undefined;
  if (settings && settings.page.startsWith("acp:")) {
    if (!activeOption) return null;
    const boolean = typeof activeOption.value === "boolean";
    const choices = optionChoices(activeOption);
    return (
      <SettingsChoices
        options={choices}
        value={String(activeOption.value)}
        disabled={locked || !choices.length}
        onChange={(value) => {
          onChange({
            id: activeOption.id,
            value: boolean ? value === "true" : value,
          });
          settings.navigate("root");
        }}
        searchLabel={activeOption.name}
        searchPlaceholder={activeOption.description ?? activeOption.name}
        emptyLabel={t("No matching options.")}
      />
    );
  }
  const modelOptions = options?.filter(isModelOption) ?? [];
  const controlOptions =
    options?.filter((option) => !isModelOption(option)) ?? [];
  const renderRow = (option: Schema<"AcpOption">) => {
    const boolean = typeof option.value === "boolean";
    const choices = optionChoices(option);
    const disabled = locked || !choices.length;
    const selectedLabel =
      choices.find(({ value }) => value === String(option.value))?.label ??
      String(option.value);
    const model = isModelOption(option);
    return (
      <div key={option.id}>
        <SettingsRow
          label={option.name}
          value={
            model ? (
              <ModelIdentity
                name={selectedLabel}
                modelIdentity={acpModelIdentity(
                  selectedLabel,
                  String(option.value),
                  preset,
                )}
              />
            ) : (
              selectedLabel
            )
          }
          disabled={disabled}
          onClick={() => {
            if (settings)
              settings.navigate(
                `acp:${option.id}`,
                model ? t("Select model") : option.name,
              );
            else
              setFallbackExpanded(
                fallbackExpanded === option.id ? undefined : option.id,
              );
          }}
        />
        {!settings && fallbackExpanded === option.id && (
          <SettingsChoices
            options={choices}
            value={String(option.value)}
            disabled={disabled}
            onChange={(value) => {
              onChange({
                id: option.id,
                value: boolean ? value === "true" : value,
              });
              setFallbackExpanded(undefined);
            }}
            searchLabel={option.name}
            searchPlaceholder={option.description ?? option.name}
            emptyLabel={t("No matching options.")}
          />
        )}
      </div>
    );
  };
  return (
    <>
      {loading && !options && (
        <p className={panelStyles.optionHint} role="status">
          {t("Loading Agent options…")}
        </p>
      )}
      {modelOptions.map(renderRow)}
      {controlOptions.length > 0 && (
        <div className={panelStyles.modelControls}>
          {controlOptions.map(renderRow)}
        </div>
      )}
      <ErrorNotice error={error} />
    </>
  );
}
