import { useQuery } from "@tanstack/react-query";
import { Button, FormField, Input, Label, Switch } from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ResourcePicker } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";
import { DateTimeField } from "../../shared/forms";
import { formatLocalDateTime } from "../../shared/local-date-time";
import { validHistory } from "./organization-form";
import styles from "./space-selection.module.css";

type History = Schema["OrganizationHistory"];
export function OrganizationHistoryField({
  value,
  onChange,
  automatic = false,
  readOnly = false,
}: {
  value: History | null;
  onChange: (value: History | null) => void;
  automatic?: boolean;
  readOnly?: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace(),
    id = useId();
  const sessions = useQuery({
    queryKey: ["organization-source-sessions", workspace.id],
    enabled: !!value && !readOnly,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/sessions", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  return (
    <div className="grid gap-4">
      <div className={styles.participation}>
        <div>
          <Label htmlFor={id}>{t("Include conversation history")}</Label>
          <p>
            {t(
              "Only sealed runs from explicitly selected sessions are read. Retention and current access still apply.",
            )}
          </p>
        </div>
        <Switch
          id={id}
          checked={!!value}
          disabled={readOnly}
          onCheckedChange={(checked) =>
            onChange(
              checked
                ? { session_ids: [], max_runs: 100, after: null, before: null }
                : null,
            )
          }
        />
      </div>
      {value && (
        <>
          {!readOnly && (
            <ResourcePicker
              label={t("Select source sessions")}
              searchLabel={t("Search sessions…")}
              emptyLabel={t("No sessions available")}
              items={(sessions.data ?? []).map((session) => ({
                id: session.id,
                name: session.preview?.input_text || t("Untitled session"),
                detail: session.id,
                disabled:
                  value.session_ids.length >= 100 &&
                  !value.session_ids.includes(session.id),
              }))}
              selected={new Set(value.session_ids)}
              loading={sessions.isPending}
              manageHref={`${basePath}/sessions`}
              manageLabel={t("Sessions")}
              onToggle={(session, checked) =>
                onChange({
                  ...value,
                  session_ids: checked
                    ? [...value.session_ids, session]
                    : value.session_ids.filter((item) => item !== session),
                })
              }
            />
          )}
          <ErrorNotice
            error={sessions.error}
            retry={() => void sessions.refetch()}
          />
          <ul className="grid gap-2 text-xs">
            {value.session_ids.map((session) => (
              <li
                key={session}
                className="flex items-center justify-between gap-3"
              >
                <span className="truncate">
                  {sessions.data?.find((item) => item.id === session)?.preview
                    ?.input_text || session}
                </span>
                {!readOnly && (
                  <Button
                    type="button"
                    size="xs"
                    variant="ghost"
                    aria-label={t("Remove {{name}}", { name: session })}
                    onClick={() =>
                      onChange({
                        ...value,
                        session_ids: value.session_ids.filter(
                          (item) => item !== session,
                        ),
                      })
                    }
                  >
                    {t("Remove")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          <div className="grid gap-4 sm:grid-cols-2">
            <DateTimeField
              label={t(automatic ? "Sealed after" : "Sealed after (optional)")}
              hint={t("Browser local time; saved with an explicit UTC offset.")}
              disabled={readOnly}
              value={
                value.after ? formatLocalDateTime(new Date(value.after)) : ""
              }
              onValueChange={(text) =>
                onChange({
                  ...value,
                  after: text ? new Date(text).toISOString() : null,
                })
              }
            />
            <DateTimeField
              label={t("Sealed before (optional)")}
              disabled={readOnly}
              value={
                value.before ? formatLocalDateTime(new Date(value.before)) : ""
              }
              onValueChange={(text) =>
                onChange({
                  ...value,
                  before: text ? new Date(text).toISOString() : null,
                })
              }
            />
          </div>
          <FormField
            label={t("Runs per batch")}
            readOnly={readOnly}
            description={t(
              automatic
                ? "Successful rounds consume each source once. Failed rounds leave sources eligible for retry."
                : "Requests exceeding this bound are refused. Narrow the window or raise the bound.",
            )}
          >
            <Input
              type="number"
              min={1}
              max={1000}
              required
              value={value.max_runs ?? 100}
              onChange={(event) =>
                onChange({ ...value, max_runs: Number(event.target.value) })
              }
            />
          </FormField>
          {!validHistory(value, automatic) && (
            <p role="alert" className="text-xs text-destructive-foreground">
              {t(
                "Select 1–100 unique sessions, a valid ordered time window and 1–1000 runs. Automatic history also requires a start time.",
              )}
            </p>
          )}
        </>
      )}
    </div>
  );
}
