import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { useEffect, useRef } from "react";
import { useTranslation } from "../i18n";
import { errorMessage } from "../i18n/error-message";
import {
  acpModelIdentity,
  ModelIcon,
  ModelIdentity,
} from "../shell/model-identity";
import { ErrorNotice } from "../shell/ui";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import type { ComposerBlock } from "./composer";
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

export function useAcpOptionChange(
  threadId: string,
  active: boolean,
  agentId?: string,
  continuation?: string | null,
) {
  const transport = useTransport();
  const queries = useQueryClient();
  const scopeKey = JSON.stringify([threadId, agentId, continuation]);
  const activeScope = useRef(scopeKey);
  activeScope.current = scopeKey;
  const activeRun = useRef(active);
  activeRun.current = active;
  const mutation = useMutation({
    mutationFn: ({
      change,
      threadId: targetThreadId,
    }: {
      change: Schema<"AcpOptionChange">;
      threadId: string;
      scopeKey: string;
      queryKey: readonly [
        "thread",
        string,
        "acp-options",
        string | undefined,
        string | null | undefined,
      ];
    }) =>
      result(
        transport.client.PUT("/api/threads/{thread_id}/acp-options", {
          params: { path: { thread_id: targetThreadId } },
          body: change,
        }),
      ),
    onSuccess: async (updated, vars) => {
      await queries.cancelQueries({ queryKey: vars.queryKey, exact: true });
      if (activeScope.current !== vars.scopeKey || activeRun.current) return;
      queries.setQueryData(vars.queryKey, updated);
      void queries.invalidateQueries({
        queryKey: ["thread", vars.threadId, "context-usage"],
      });
    },
  });
  const reset = mutation.reset;
  useEffect(() => {
    reset();
  }, [scopeKey, active, reset]);
  const matchesScope = mutation.variables?.scopeKey === scopeKey;
  const isPending = !active && mutation.isPending && matchesScope;
  const error = !active && matchesScope ? mutation.error : null;
  const mutate = (change: Schema<"AcpOptionChange">) => {
    if (active) return;
    mutation.mutate({
      change,
      threadId,
      scopeKey,
      queryKey: [
        "thread",
        threadId,
        "acp-options",
        agentId,
        continuation,
      ] as const,
    });
  };
  const retry = () => {
    if (active || !matchesScope || !mutation.variables) return;
    mutation.mutate(mutation.variables);
  };
  return {
    mutate,
    retry,
    reset,
    isPending,
    error,
  };
}

export function acpRunBlock({
  agentName,
  hasData,
  isFetching,
  isError,
  error,
  onRetryLoad,
  updating,
  updateError,
  onRetryUpdate,
  onDismissUpdateError,
  acpAuthPath,
  t,
}: {
  agentName: string;
  hasData: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  onRetryLoad: () => void;
  updating: boolean;
  updateError: unknown;
  onRetryUpdate?: () => void;
  onDismissUpdateError: () => void;
  acpAuthPath?: string;
  t: TFunction;
}): ComposerBlock | null {
  if (updating)
    return {
      kind: "loading",
      message: t("Updating {{agent}} options…", { agent: agentName }),
    };
  if (updateError)
    return {
      kind: "error",
      message: t("Could not update {{agent}} options.", { agent: agentName }),
      detail: errorMessage(updateError, t),
      showWhenEmpty: true,
      action: onRetryUpdate
        ? { label: t("Retry"), onClick: onRetryUpdate }
        : undefined,
      secondaryAction: {
        label: t("Keep current"),
        onClick: onDismissUpdateError,
      },
    };
  // Check isFetching before isError so TanStack Query v5 retries immediately
  // transition back to loading while a prior error is still retained.
  if (isFetching || (!hasData && !isError))
    return {
      kind: "loading",
      message: hasData
        ? t("Refreshing {{agent}} options…", { agent: agentName })
        : t("Loading {{agent}} options…", { agent: agentName }),
    };
  if (isError || error) {
    const code = error instanceof ApiError ? error.code : undefined;
    if (code === "acp_auth_required")
      return {
        kind: "action_required",
        message: t("Sign in to {{agent}} before sending.", {
          agent: agentName,
        }),
        showWhenEmpty: true,
        action: acpAuthPath
          ? { label: t("Open Agent authentication"), to: acpAuthPath }
          : { label: t("Retry"), onClick: onRetryLoad },
        secondaryAction: acpAuthPath
          ? { label: t("Retry"), onClick: onRetryLoad }
          : undefined,
      };
    if (code === "acp_agent_missing")
      return {
        kind: "error",
        message: t(
          "The selected ACP agent is no longer available. Choose another agent or check Settings.",
        ),
        showWhenEmpty: true,
        action: { label: t("Retry"), onClick: onRetryLoad },
      };
    return {
      kind: "error",
      message: t("Could not load {{agent}} options.", { agent: agentName }),
      detail: errorMessage(error, t),
      showWhenEmpty: true,
      action: { label: t("Retry"), onClick: onRetryLoad },
    };
  }
  return null;
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
  change,
}: {
  threadId: string;
  active: boolean;
  agentId?: string;
  preset?: string | null;
  continuation?: string | null;
  options: ReturnType<typeof useAcpOptions>;
  change?: ReturnType<typeof useAcpOptionChange>;
}) {
  const internalChange = useAcpOptionChange(
    threadId,
    active,
    agentId,
    continuation,
  );
  const select = change ?? internalChange;
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
