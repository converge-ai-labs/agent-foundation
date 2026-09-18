import { DotsThreeIcon, PowerIcon, TrashIcon } from "@phosphor-icons/react";
import {
  Button,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
} from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import { useIdempotency } from "../../shared/idempotency";

/**
 * Availability and removal for one application account. Both the bot detail
 * page and the application account page present these from one overflow menu.
 */
export function AccountActions({
  account,
  reload,
  onDeleted,
  leading,
  label,
}: {
  account: Schema["Account"];
  reload: () => Promise<void> | void;
  onDeleted: () => void;
  leading?: ReactNode;
  label?: string;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    key = useIdempotency();
  const manage = can("application_account.manage");
  if (!manage && !leading) return null;
  const menuLabel = label ?? t("More actions");
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            variant="outline"
            size="icon"
            type="button"
            aria-label={menuLabel}
            title={menuLabel}
          />
        }
      >
        <DotsThreeIcon size={16} />
      </MenuTrigger>
      <MenuPopup align="end">
        {leading}
        {manage && (
          <>
            {leading && <MenuSeparator />}
            <Confirm
              subject={account.name}
              title={t(
                account.status === "active"
                  ? "Disable account"
                  : "Enable account",
              )}
              description={t(
                "Administrative availability controls reception and provider dispatch.",
              )}
              triggerElement={
                <MenuItem closeOnClick={false}>
                  <PowerIcon size={14} />
                  {t(account.status === "active" ? "Disable" : "Enable")}
                </MenuItem>
              }
              action={async () => {
                const action =
                    account.status === "active" ? "disable" : "enable",
                  body = { expected_version: account.version };
                await client.http.POST(
                  "/api/v1/application-accounts/{account_id}/{action}",
                  {
                    params: {
                      path: { account_id: account.id, action },
                      header: commandHeaders(
                        workspace.id,
                        key.forBody({ action, ...body }),
                      ),
                    },
                    body,
                  },
                );
                await reload();
              }}
            />
            <MenuSeparator />
            <Confirm
              subject={account.name}
              title={t("Delete application account")}
              description={t(
                "This makes the identity unavailable and clears its credentials. Retained run evidence keeps its original identity.",
              )}
              triggerElement={
                <MenuItem closeOnClick={false} variant="destructive">
                  <TrashIcon size={14} />
                  {t("Delete")}
                </MenuItem>
              }
              danger
              action={async () => {
                await client.http.DELETE(
                  "/api/v1/application-accounts/{account_id}",
                  {
                    params: {
                      path: { account_id: account.id },
                      query: { expected_version: account.version },
                    },
                  },
                );
                onDeleted();
              }}
            />
          </>
        )}
      </MenuPopup>
    </Menu>
  );
}
