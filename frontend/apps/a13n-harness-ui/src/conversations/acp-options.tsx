import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
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

export function acpModelOption(options?: Schema<"AcpOption">[]) {
  return options?.find(
    (option) => option.category === "model" || option.id === "model",
  );
}

export function acpOptionLabel(
  option?: Schema<"AcpOption">,
  emptyLabel?: string,
) {
  if (option?.value === "") return emptyLabel;
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
    onSuccess: async (updated) => {
      await queries.cancelQueries({ queryKey: key, exact: true });
      queries.setQueryData(key, updated);
      void queries.invalidateQueries({
        queryKey: ["thread", threadId, "context-usage"],
      });
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
  const isModelOption = (option: Schema<"AcpOption">) =>
    option.category === "model" || option.id === "model";
  const optionChoices = (option: Schema<"AcpOption">) => {
    if (typeof option.value === "boolean")
      return [
        { label: t("Enabled"), value: "true" },
        { label: t("Disabled"), value: "false" },
      ];
    const model = isModelOption(option);
    return (option.choices ?? [])
      .filter(({ value }) => value !== "")
      .map(({ name, value }) => ({
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
  const onSubpage = Boolean(settings?.page.startsWith("acp:"));
  const activeOption = onSubpage
    ? options?.find((option) => `acp:${option.id}` === settings?.page)
    : undefined;
  useEffect(() => {
    if (settings && onSubpage && !activeOption && !loading && !error && options)
      settings.navigate("root");
  }, [activeOption, error, loading, onSubpage, options, settings]);
  if (settings && onSubpage) {
    if (!activeOption)
      return (
        <>
          {loading && (
            <p className={panelStyles.optionHint} role="status">
              {t("Loading Agent options…")}
            </p>
          )}
          <ErrorNotice error={error} />
        </>
      );
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
  if (settings && settings.page !== "root") return null;
  const modelOptions = options?.filter(isModelOption) ?? [];
  const controlOptions =
    options?.filter((option) => !isModelOption(option)) ?? [];
  const renderRow = (option: Schema<"AcpOption">) => {
    const boolean = typeof option.value === "boolean";
    const choices = optionChoices(option);
    const disabled = locked || !choices.length;
    const selectedLabel = boolean
      ? choices.find(({ value }) => value === String(option.value))?.label
      : acpOptionLabel(option, t("Not selected"));
    const model = isModelOption(option);
    const hasModelValue = model && option.value !== "";
    return (
      <SettingsRow
        key={option.id}
        label={option.name}
        value={
          model ? (
            <ModelIdentity
              name={selectedLabel}
              modelIdentity={acpModelIdentity(
                hasModelValue ? selectedLabel : undefined,
                hasModelValue ? String(option.value) : undefined,
                preset,
              )}
            />
          ) : (
            selectedLabel
          )
        }
        disabled={disabled}
        onClick={() =>
          settings?.navigate(
            `acp:${option.id}`,
            model ? t("Select model") : option.name,
          )
        }
      />
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
