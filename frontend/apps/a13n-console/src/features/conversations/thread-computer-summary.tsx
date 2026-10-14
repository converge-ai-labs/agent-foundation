import { useQuery } from "@tanstack/react-query";
import { MonitorIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationQueries } from "./api";
import {
  computerPermissionLabels,
  computerPermissionMode,
} from "./computer-permissions";

/** Desired permissions for later Runs, never an assertion of desktop readiness. */
export function ThreadComputerSummary({
  threadId,
  onConfigure,
}: {
  threadId: string;
  onConfigure: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const mounts = useQuery(
    conversationQueries(client, workspace.id).environments(threadId),
  );
  const modes = new Set(
    mounts.data?.map((mount) =>
      computerPermissionMode(mount.permission_ceiling?.operations ?? []),
    ),
  );
  const label = mounts.isPending
    ? "Computer permissions: loading…"
    : mounts.isError
      ? "Computer permissions: unavailable"
      : !mounts.data?.length
        ? "Computer permissions: no environment"
        : modes.size === 1
          ? computerPermissionLabels[[...modes][0]]
          : "Computer permissions: varies by environment";
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={onConfigure}
      title={t("Configure Computer permissions for later runs")}
    >
      <MonitorIcon size={14} aria-hidden="true" />
      {t(label)}
    </Button>
  );
}
