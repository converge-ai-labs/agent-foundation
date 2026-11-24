import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "../i18n";
import { ErrorNotice } from "../shell/ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { SettingsChoices, SettingsRow } from "./composer-settings";

export function AcpOptions({
  threadId,
  active,
  continuation,
}: {
  threadId: string;
  active: boolean;
  continuation?: string | null;
}) {
  const { t } = useTranslation();
  const transport = useTransport();
  const queries = useQueryClient();
  const [expanded, setExpanded] = useState<string>();
  const key = ["thread", threadId, "acp-options", continuation] as const;
  const params = { path: { thread_id: threadId } };
  const options = useQuery({
    queryKey: key,
    queryFn: () =>
      result(
        transport.client.GET("/api/threads/{thread_id}/acp-options", {
          params,
        }),
      ),
  });
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
      setExpanded(undefined);
    },
  });
  return (
    <>
      {options.data?.map((option) => {
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
        const disabled = active || select.isPending || !choices.length;
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
                onChange={(value) =>
                  select.mutate({
                    id: option.id,
                    value: boolean ? value === "true" : value,
                  })
                }
                searchLabel={option.name}
                searchPlaceholder={option.description ?? option.name}
                emptyLabel={t("No matching options.")}
              />
            )}
          </div>
        );
      })}
      <ErrorNotice error={select.error ?? options.error} />
    </>
  );
}
