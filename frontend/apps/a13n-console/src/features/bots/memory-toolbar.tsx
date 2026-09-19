import type { BotAccount } from "./account";
import { Button, StatusPill } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { GroupMemorySettings } from "./memory-settings";
import { MemoryOperations } from "./memory-operations";
import styles from "./bots.module.css";

export function GroupMemoryActions({
  account,
  scopeId,
  scope,
  target,
}: {
  account: BotAccount;
  scopeId: string;
  scope?: Schema["Scope"];
  target?: Schema["AccountTarget"];
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace(),
    client = useClient();
  const [settings, setSettings] = useState(false),
    [operations, setOperations] = useState(false);
  const settingsRef = useRef<HTMLButtonElement>(null),
    pendingRef = useRef<HTMLButtonElement>(null);
  const canManage = can("bot_memory.share");
  const pending = useQuery({
    queryKey: ["bot-memory-operations", account.id, scopeId, null],
    enabled: canManage,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations",
          {
            params: { path: { account_id: account.id, scope_id: scopeId } },
            signal,
          },
        )
        .then(data),
  });
  const count = pending.data?.items.length ?? 0;
  return (
    <>
      {scope && canManage && (
        <>
          <Button
            ref={settingsRef}
            size="sm"
            variant="outline"
            onClick={() => setSettings(true)}
          >
            {t("Group memory settings")}
          </Button>
          <GroupMemorySettings
            account={account}
            initialScope={scope}
            target={target}
            dialog={{
              open: settings,
              onOpenChange: setSettings,
              finalFocus: settingsRef,
            }}
          />
        </>
      )}
      {canManage && (
        <>
          <ErrorNotice
            error={pending.error}
            retry={() => void pending.refetch()}
          />
          {count > 0 && (
            <Button
              ref={pendingRef}
              size="sm"
              variant="ghost"
              className={styles.attention}
              onClick={() => setOperations(true)}
            >
              <StatusPill variant="warning">
                {t("Memory needs attention")} · {count}
                {pending.data?.next_cursor ? "+" : ""}
              </StatusPill>
            </Button>
          )}
          <MemoryOperations
            account={account}
            scopeId={scopeId}
            dialog={{
              open: operations,
              onOpenChange: setOperations,
              finalFocus: count > 0 ? pendingRef : settingsRef,
            }}
          />
        </>
      )}
    </>
  );
}
