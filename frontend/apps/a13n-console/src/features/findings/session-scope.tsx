import {
  Button,
  Checkbox,
  Field,
  FieldLabel,
  Input,
  Label,
  Popover,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import { XIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";
import { conversationQueries } from "../conversations/api";
import { sessionTitle, sessionPath } from "../conversations/session-identity";
import styles from "./findings.module.css";

function scopeSessionTitle(session: Schema["SessionView"]) {
  if (session.labels?.["a13n.finding"])
    return session.title || session.preview?.agent_name || session.id;
  return sessionTitle(session) || session.preview?.input_text || session.id;
}

export function SessionScopePicker({
  selected,
  onChange,
  disabled,
}: {
  selected: Map<string, string>;
  onChange: (value: Map<string, string>) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace();
  const [open, setOpen] = useState(false),
    [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setFilter(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  const page = useCursor(filter);
  const query = useQuery({
    ...conversationQueries(client, workspace.id).sessions(page.cursor, {
      q: filter || undefined,
    }),
    enabled: open,
  });
  const cache = useQueryClient();
  const addById = useMutation({
    mutationFn: (id: string) =>
      cache.fetchQuery(conversationQueries(client, workspace.id).session(id)),
    onSuccess: (session) => {
      onChange(new Map(selected).set(session.id, scopeSessionTitle(session)));
      setSearch("");
    },
  });
  return (
    <Field>
      <FieldLabel htmlFor="finding-session-picker">{t("Sessions")}</FieldLabel>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          id="finding-session-picker"
          render={
            <Button type="button" variant="outline" disabled={disabled} />
          }
        >
          {selected.size
            ? t("{{count}} selected", { count: selected.size })
            : t("Select sessions")}
        </PopoverTrigger>
        <PopoverPopup align="start" className={styles.sessionPicker}>
          <Input
            type="search"
            aria-label={t("Search sessions or enter a session ID")}
            placeholder={t("Search sessions or enter a session ID")}
            autoFocus
            value={search}
            disabled={addById.isPending}
            onChange={(e) => {
              addById.reset();
              setSearch(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
              e.preventDefault();
              const id = search.trim();
              if (
                id.startsWith("sess_") &&
                selected.size < 20 &&
                !addById.isPending
              )
                addById.mutate(id);
            }}
          />
          <ErrorNotice error={addById.error} />
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
          <div className={styles.sessionOptions}>
            {query.data?.items.map((item) => {
              const title = scopeSessionTitle(item);
              return (
                <Label className={styles.sessionOption} key={item.id}>
                  <Checkbox
                    checked={selected.has(item.id)}
                    disabled={
                      addById.isPending ||
                      (!selected.has(item.id) && selected.size >= 20)
                    }
                    onCheckedChange={(checked) => {
                      const next = new Map(selected);
                      if (checked) next.set(item.id, title);
                      else next.delete(item.id);
                      onChange(next);
                    }}
                  />
                  <span title={title}>{title}</span>
                </Label>
              );
            })}
            {query.isPending && t("Loading…")}
            {query.isSuccess && !query.data.items.length && t("No matches")}
          </div>
          <Pagination page={page} next={query.data?.next_cursor} />
        </PopoverPopup>
      </Popover>
      {selected.size > 0 && (
        <ul className={styles.sessionTags}>
          {Array.from(selected, ([id, title]) => (
            <li key={id}>
              <Link
                className={styles.link}
                to={`${sessionPath(basePath, id)}?view=debug`}
                target="_blank"
                rel="noopener noreferrer"
              >
                {title}
              </Link>
              <Button
                tooltip={false}
                size="icon-sm"
                variant="ghost"
                disabled={disabled}
                type="button"
                aria-label={t("Remove {{name}}", { name: title })}
                onClick={() => {
                  const next = new Map(selected);
                  next.delete(id);
                  onChange(next);
                }}
              >
                <XIcon aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Field>
  );
}

export function SessionTraceCount({
  id,
  count,
}: {
  id: string;
  count: number;
}) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const session = useQuery(
    conversationQueries(client, workspace.id).session(id),
  );
  return (
    <li>
      <Link className={styles.link} to={sessionPath(basePath, id)}>
        {session.data ? scopeSessionTitle(session.data) : id}
      </Link>{" "}
      <span>
        {t("Selected traces")} · {count}
      </span>
    </li>
  );
}
