import { Checkbox, Label } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "./bots.module.css";

export function useMemoryGroups(account: Schema["Account"], enabled = true) {
  const client = useClient();
  return useQuery({
    queryKey: [
      "bot-memory-group-picker",
      account.id,
      account.memory?.provider_id,
    ],
    enabled: enabled && !!account.memory,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/application-accounts/{account_id}/memory-scopes", {
            params: {
              path: { account_id: account.id },
              query: {
                provider_id: account.memory!.provider_id,
                cursor,
                limit: 100,
              },
            },
            signal,
          })
          .then(data),
      ),
  });
}

export function GroupPicker({
  groups,
  value,
  onChange,
  exclude,
  disabled = false,
  label = "Recipient groups",
  description = "Sharing allows retrieval in these groups. It does not send a chat message.",
}: {
  groups: ReturnType<typeof useMemoryGroups>;
  value: string[];
  onChange: (value: string[]) => void;
  exclude?: string;
  disabled?: boolean;
  label?: string;
  description?: string;
}) {
  const { t } = useTranslation();
  const known = groups.data ?? [];
  return (
    <fieldset className={styles.groupPicker} disabled={disabled}>
      <legend>{t(label)}</legend>
      <p>{t(description)}</p>
      <ErrorNotice error={groups.error} retry={() => void groups.refetch()} />
      {groups.isPending && <Loading />}
      {!groups.error &&
        known
          .filter((item) => item.id !== exclude)
          .map((group) => {
            const eligible =
              group.enabled && ["public", "private"].includes(group.audience);
            return (
              <Label key={group.id} className={styles.groupOption}>
                <Checkbox
                  checked={value.includes(group.id)}
                  disabled={
                    disabled || (!eligible && !value.includes(group.id))
                  }
                  onCheckedChange={(checked) =>
                    onChange(
                      checked
                        ? [...value, group.id]
                        : value.filter((id) => id !== group.id),
                    )
                  }
                />
                <span>
                  {group.name}
                  <small>
                    {t(
                      group.audience === "private"
                        ? "Private group"
                        : group.audience === "public"
                          ? "Public group"
                          : "Not eligible for sharing",
                    )}
                    {!group.enabled ? ` · ${t("Disabled")}` : ""}
                  </small>
                </span>
              </Label>
            );
          })}
      {!groups.isPending &&
        !groups.error &&
        value
          .filter((id) => !known.some((group) => group.id === id))
          .map((id) => (
            <Label key={id} className={styles.groupOption}>
              <Checkbox
                checked
                onCheckedChange={() =>
                  onChange(value.filter((item) => item !== id))
                }
              />
              <span>
                {t("Unavailable group")}
                <small>{id}</small>
              </span>
            </Label>
          ))}
      {!groups.isPending &&
        !groups.error &&
        !known.some(
          (group) =>
            group.id !== exclude &&
            group.enabled &&
            ["public", "private"].includes(group.audience),
        ) && (
          <p>
            {t(
              "No eligible recipient groups. Configure and verify another group first.",
            )}
          </p>
        )}
    </fieldset>
  );
}
