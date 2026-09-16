import type { BotAccount } from "./account";
import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { DotsThreeIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { GroupMemorySettings } from "./memory-settings";
import { MemoryOperations } from "./memory-operations";
import { MemoryPublications } from "./memory-publications";
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
  const [active, setActive] = useState<
    "settings" | "publications" | "operations" | null
  >(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const pendingRef = useRef<HTMLButtonElement>(null);
  const [fromNotice, setFromNotice] = useState(false);
  const canShare = can("bot_memory.share");
  const pending = useQuery({
    queryKey: ["bot-memory-operations", account.id, scopeId, null],
    enabled: canShare,
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
  function control(name: NonNullable<typeof active>) {
    return {
      open: active === name,
      onOpenChange: (open: boolean) => setActive(open ? name : null),
      finalFocus: fromNotice && count > 0 ? pendingRef : menuRef,
    };
  }
  return (
    <>
      {canShare && !pending.error && count > 0 && (
        <Button
          ref={pendingRef}
          variant="ghost"
          size="sm"
          className={styles.pendingMemory}
          onClick={() => {
            setFromNotice(true);
            setActive("operations");
          }}
        >
          {t("Pending operations")} · {count}
          {pending.data?.next_cursor ? "+" : ""}
        </Button>
      )}
      <Menu>
        <MenuTrigger
          render={
            <Button
              ref={menuRef}
              size="sm"
              variant="ghost"
              aria-label={t("Group memory actions")}
            />
          }
        >
          <DotsThreeIcon size={20} aria-hidden="true" />
        </MenuTrigger>
        <MenuPopup align="end">
          {scope && (
            <MenuItem
              onClick={() => {
                setFromNotice(false);
                setActive("settings");
              }}
            >
              {t("This group's memory settings")}
            </MenuItem>
          )}
          {canShare && (
            <MenuItem
              onClick={() => {
                setFromNotice(false);
                setActive("publications");
              }}
            >
              {t("Shared content")}
            </MenuItem>
          )}
          {canShare && (
            <MenuItem
              onClick={() => {
                setFromNotice(false);
                setActive("operations");
              }}
            >
              {t("Pending operations")}
            </MenuItem>
          )}
        </MenuPopup>
      </Menu>
      {scope && (
        <GroupMemorySettings
          account={account}
          initialScope={scope}
          target={target}
          dialog={control("settings")}
        />
      )}
      {canShare && (
        <>
          <MemoryPublications
            account={account}
            scopeId={scopeId}
            dialog={control("publications")}
          />
          <MemoryOperations
            account={account}
            scopeId={scopeId}
            dialog={control("operations")}
          />
        </>
      )}
    </>
  );
}
