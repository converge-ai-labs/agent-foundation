import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "../i18n";
import { ErrorNotice } from "../shell/ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { SettingsChoices, SettingsRow } from "./composer-settings";

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
  continuation,
  options,
}: {
  threadId: string;
  active: boolean;
  agentId?: string;
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
      loading={options.isFetching}
      disabled={active || select.isPending}
      error={select.error ?? options.error}
      onChange={select.mutate}
    />
  );
}

export function AcpOptionFields({
  options,
  loading,
  disabled: locked,
  error,
  onChange,
}: {
  options?: Schema<"AcpOption">[];
  loading: boolean;
  disabled: boolean;
  error: unknown;
  onChange: (change: Schema<"AcpOptionChange">) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<string>();
  return (
    <>
      {loading && !options && (
        <p role="status">{t("Loading Agent options…")}</p>
      )}
      {options?.map((option) => {
        const boolean = typeof option.value === "boolean";
        const choices = boolean
          ? [
              { label: t("Enabled"), value: "true" },
              { label: t("Disabled"), value: "false" },
            ]
          : (option.choices ?? []).map(({ name, value }) => ({
              label: name,
              value,
            }));
        const disabled = locked || !choices.length;
        return (
          <div key={option.id}>
            <SettingsRow
              label={option.name}
              value={
                choices.find(({ value }) => value === String(option.value))
                  ?.label ?? String(option.value)
              }
              disabled={disabled}
              onClick={() =>
                setExpanded(expanded === option.id ? undefined : option.id)
              }
            />
            {expanded === option.id && (
              <SettingsChoices
                options={choices}
                value={String(option.value)}
                disabled={disabled}
                onChange={(value) => {
                  onChange({
                    id: option.id,
                    value: boolean ? value === "true" : value,
                  });
                  setExpanded(undefined);
                }}
                searchLabel={option.name}
                searchPlaceholder={option.description ?? option.name}
                emptyLabel={t("No matching options.")}
              />
            )}
          </div>
        );
      })}
      <ErrorNotice error={error} />
    </>
  );
}
