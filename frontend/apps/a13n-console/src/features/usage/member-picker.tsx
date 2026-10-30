import { useQuery } from "@tanstack/react-query";
import { CaretDownIcon, CheckIcon } from "@phosphor-icons/react";
import { Button, Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import styles from "./usage.module.css";

export function MemberPicker({
  window,
  member,
  onSelect,
}: {
  window: { start: string; end: string };
  member: Schema["MemberUsage"] | null;
  onSelect: (member: Schema["MemberUsage"] | null) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={`${t("Member")}: ${member?.name ?? t("All members")}`}
        render={<Button variant="outline" className={styles.memberTrigger} />}
      >
        <span>{t("Member")}</span>
        <strong>{member?.name ?? t("All members")}</strong>
        <CaretDownIcon aria-hidden="true" size={14} />
      </PopoverTrigger>
      <PopoverPopup align="start" className={styles.memberPicker}>
        {open && (
          <MemberOptions
            window={window}
            member={member}
            onSelect={(value) => {
              onSelect(value);
              setOpen(false);
            }}
          />
        )}
      </PopoverPopup>
    </Popover>
  );
}

function MemberOptions({
  window,
  member,
  onSelect,
}: {
  window: { start: string; end: string };
  member: Schema["MemberUsage"] | null;
  onSelect: (member: Schema["MemberUsage"] | null) => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const page = useCursor(window);
  const query = useQuery({
    queryKey: [
      "usage",
      workspace.id,
      "members",
      window,
      undefined,
      page.cursor,
    ],
    queryFn: async ({ signal }) =>
      data(
        await client.workspace(workspace.id).GET("/api/v1/usage/members", {
          params: { query: { ...window, cursor: page.cursor, limit: 50 } },
          signal,
        }),
      ),
  });
  return (
    <>
      <button
        type="button"
        className={styles.memberOption}
        aria-pressed={!member}
        onClick={() => onSelect(null)}
      >
        {t("All members")}
        {!member && <CheckIcon aria-hidden="true" />}
      </button>
      <div className={styles.memberOptions}>
        {query.isPending ? (
          <div className={styles.pickerHint}>
            <InlineLoading />
          </div>
        ) : query.error ? (
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        ) : (
          <>
            {query.data.items.map((item) => (
              <button
                key={item.principal_id}
                type="button"
                className={styles.memberOption}
                aria-label={`${item.name} · ${t(item.kind === "user" ? "User" : "Service account")}${item.status === "disabled" ? ` · ${t("Disabled")}` : ""}`}
                aria-pressed={item.principal_id === member?.principal_id}
                onClick={() => onSelect(item)}
              >
                <span className={styles.identity}>
                  <strong>{item.name}</strong>
                  <span>
                    {t(item.kind === "user" ? "User" : "Service account")}
                    {item.status === "disabled" ? ` · ${t("Disabled")}` : ""}
                  </span>
                </span>
                {item.principal_id === member?.principal_id && (
                  <CheckIcon aria-hidden="true" />
                )}
              </button>
            ))}
            {!query.data.items.length && (
              <p className={styles.pickerHint}>
                {t("No usage in this period")}
              </p>
            )}
          </>
        )}
      </div>
      {query.data && (page.previous || query.data.next_cursor) && (
        <div className={styles.pickerPagination}>
          <Pagination page={page} next={query.data.next_cursor} />
        </div>
      )}
    </>
  );
}
