import { Button, FormField, Input } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "./connect.module.css";

export function ConversationPicker({
  account,
  value,
  onChange,
  disabled = false,
}: {
  account: Schema["Account"];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation();
  const [open, setOpen] = useState(false),
    [cursors, setCursors] = useState<string[]>([]),
    [filter, setFilter] = useState("");
  const cursor = cursors.at(-1);
  const query = useQuery({
    queryKey: [
      "bot-discovery",
      account.id,
      account.credential_generation,
      cursor,
    ],
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/application-accounts/{account_id}/bot/conversations", {
          params: {
            path: { account_id: account.id },
            query: { limit: 100, cursor },
          },
          signal,
        })
        .then(data),
  });
  const items = query.data?.items.filter((item) =>
    `${item.name} ${item.id}`
      .toLocaleLowerCase()
      .includes(filter.toLocaleLowerCase()),
  );
  return (
    <div className={styles.picker}>
      <FormField
        label={t(
          account.provider_key === "github"
            ? "Pilot repository ID"
            : "Pilot conversation ID",
        )}
        description={t(
          account.provider_key === "github"
            ? "Choose an accessible repository or enter its numeric repository ID."
            : "Use the exact channel or chat ID. Discovery only lists conversations visible to this app; a missing result does not mean the conversation does not exist.",
        )}
      >
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          maxLength={128}
          required
          disabled={disabled}
        />
      </FormField>
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {t(
          account.provider_key === "github"
            ? "Find repositories"
            : "Find conversations",
        )}
      </Button>
      {open && (
        <section aria-label={t("Available conversations")}>
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
          {query.isFetching ? (
            <Loading variant="table" columns={2} rows={3} />
          ) : (
            !query.error && (
              <>
                <FormField label={t("Filter this page")}>
                  <Input
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                    disabled={disabled}
                  />
                </FormField>
                <ul className={styles.candidates}>
                  {items?.map((item) => (
                    <li key={item.id}>
                      <Button
                        type="button"
                        variant={value === item.id ? "secondary" : "ghost"}
                        disabled={disabled}
                        aria-pressed={value === item.id}
                        onClick={() => onChange(item.id)}
                      >
                        <span>{item.name}</span>
                        <code>{item.id}</code>
                      </Button>
                    </li>
                  ))}
                </ul>
                {items?.length === 0 && (
                  <p>
                    {t(
                      "No matching conversations on this page. You can still enter an ID above.",
                    )}
                  </p>
                )}
              </>
            )
          )}
          <div className={styles.actions}>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || !cursors.length || query.isFetching}
              onClick={() => {
                setCursors(cursors.slice(0, -1));
                setFilter("");
              }}
            >
              {t("Previous")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                disabled ||
                !query.data?.cursor ||
                query.isFetching ||
                !!query.error
              }
              onClick={() => {
                if (query.data?.cursor)
                  setCursors([...cursors, query.data.cursor]);
                setFilter("");
              }}
            >
              {t("Next")}
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
