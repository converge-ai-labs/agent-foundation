import { useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { SchemaForm } from "./form";
import {
  clearSecrets,
  defaultValue,
  type FormSchema,
  type Json,
} from "./schema";
import styles from "./connectors.module.css";

export function ConnectorSetup({
  connectorId,
  onClose,
}: {
  connectorId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const [snapshot, setSnapshot] = useState<Schema<"SetupSnapshot"> | null>(
    null,
  );
  const [values, setValues] = useState<Json>({});
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const cancelPending = useRef(false);
  const [uncertain, setUncertain] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const reading = useRef(false);
  const actionPending = useRef(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const schema = snapshot?.step.form as FormSchema | null;
  const terminal =
    !!snapshot &&
    ["completed", "failed", "cancelled", "expired"].includes(
      snapshot.step.state ?? "input",
    );

  useEffect(() => {
    let active = true;
    mounted.current = true;
    const current = ++generation.current;
    setError(null);
    setValues({});
    setUncertain(false);
    setSnapshot(null);
    void result(
      client.POST("/api/connectors/{connector_id}/setup", {
        params: { path: { connector_id: connectorId } },
        body: {},
      }),
    )
      .then((next) => {
        if (!active || current !== generation.current) return;
        setSnapshot(next);
        const form = next.step.form as FormSchema | null;
        setValues(
          form
            ? {
                ...(defaultValue(form) as Record<string, Json>),
                ...(next.step.values as Record<string, Json>),
              }
            : {},
        );
      })
      .catch((failure) => {
        if (active) setError(failure);
      });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, [client, connectorId, attempt]);

  async function read() {
    if (!snapshot || reading.current || actionPending.current) return;
    reading.current = true;
    const current = generation.current;
    try {
      const next = await result(
        client.GET("/api/connector-setups/{session_id}", {
          params: { path: { session_id: snapshot.session_id } },
        }),
      );
      if (!mounted.current || current !== generation.current) return;
      setSnapshot(next);
      setUncertain(false);
      setError(null);
      if (next.step.key !== snapshot.step.key) {
        const form = next.step.form as FormSchema | null;
        setValues(
          form
            ? {
                ...(defaultValue(form) as Record<string, Json>),
                ...(next.step.values as Record<string, Json>),
              }
            : {},
        );
      }
      void cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      if (mounted.current && current === generation.current) setError(failure);
    } finally {
      reading.current = false;
    }
  }

  useEffect(() => {
    const wait = snapshot?.step.wait;
    if (!wait || terminal || busy || uncertain) return;
    const deadline = Math.min(
      Date.parse(wait.expires_at),
      Date.parse(snapshot.expires_at),
    );
    const remaining = deadline - Date.now();
    const timer = setTimeout(
      () => {
        void read();
      },
      Math.max(0, Math.min(wait.poll_after_ms, remaining)),
    );
    return () => clearTimeout(timer);
  });

  useEffect(() => {
    if (!snapshot || terminal) return;
    const refresh = () => {
      if (document.visibilityState === "visible") void read();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  });

  async function act(action: string) {
    if (
      !snapshot ||
      uncertain ||
      cancelPending.current ||
      (actionPending.current && action !== "cancel")
    )
      return;
    if (action === "cancel") {
      cancelPending.current = true;
      setCancelling(true);
    }
    actionPending.current = true;
    const current = ++generation.current;
    setBusy(true);
    setError(null);
    // Private fields live only in this component and the request body, never a Query mutation/cache.
    const submitted = values as Record<string, Json>;
    if (schema) setValues(clearSecrets(schema, values));
    try {
      const next = await result(
        client.POST("/api/connector-setups/{session_id}/actions", {
          params: { path: { session_id: snapshot.session_id } },
          body: {
            operation_id: `action-${crypto.randomUUID()}`,
            version: snapshot.version,
            action,
            values: submitted,
          },
        }),
      );
      if (!mounted.current || current !== generation.current) return;
      setSnapshot(next);
      if (next.step.key !== snapshot.step.key && next.step.form) {
        const form = next.step.form as FormSchema;
        setValues({
          ...(defaultValue(form) as Record<string, Json>),
          ...(next.step.values as Record<string, Json>),
        });
      }
      void cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      if (mounted.current) {
        setError(failure);
        setUncertain(true);
      }
    } finally {
      if (current === generation.current) {
        actionPending.current = false;
        cancelPending.current = false;
        if (mounted.current) {
          setBusy(false);
          setCancelling(false);
        }
      }
    }
  }

  return (
    <section className={styles.setup} aria-label={t("Connection setup")}>
      <header className={styles.header}>
        <h3>
          {snapshot ? t(snapshot.step.title) : t("Opening connection setup…")}
        </h3>
        <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
          {t("Close")}
        </Button>
      </header>
      <ErrorNotice error={error} />
      {snapshot && (
        <>
          <p className={styles.description}>
            {t(snapshot.step.description ?? "")}
          </p>
          {snapshot.step.error && (
            <p role="alert" className={styles.error}>
              {t(snapshot.step.error)}
            </p>
          )}
          {snapshot.step.wait && !terminal && (
            <p role="status">
              {t(snapshot.step.wait.message)}{" "}
              <span className={styles.description}>
                {t("Expires at {{time}}", {
                  time: new Date(
                    snapshot.step.wait.expires_at,
                  ).toLocaleTimeString(),
                })}
              </span>
            </p>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const action = (
                event.nativeEvent as SubmitEvent
              ).submitter?.getAttribute("value");
              if (action) void act(action);
            }}
          >
            {schema && !terminal && (
              <SchemaForm
                schema={schema}
                value={values}
                onChange={setValues}
                disabled={busy || uncertain}
              />
            )}
            <div className={styles.actions}>
              {!terminal &&
                !uncertain &&
                snapshot.step.actions?.map((action, index) =>
                  action.kind === "open_url" ? (
                    <Button
                      key={action.id}
                      variant="outline"
                      render={
                        <a
                          href={action.url ?? undefined}
                          target="_blank"
                          rel="noreferrer"
                        />
                      }
                    >
                      {t(action.label)}
                    </Button>
                  ) : action.kind === "copy" ? (
                    <Button
                      key={action.id}
                      variant="outline"
                      type="button"
                      onClick={() => {
                        void navigator.clipboard.writeText(action.value ?? "");
                      }}
                    >
                      {t(action.label)}
                    </Button>
                  ) : (
                    <Button
                      key={action.id}
                      type="submit"
                      value={action.id}
                      variant={
                        index === (snapshot.step.actions?.length ?? 0) - 1
                          ? "outline"
                          : "default"
                      }
                      disabled={busy}
                      onClick={
                        action.kind === "confirm"
                          ? (event) => {
                              if (!window.confirm(t(action.label)))
                                event.preventDefault();
                            }
                          : undefined
                      }
                    >
                      {t(action.label)}
                    </Button>
                  ),
                )}
              {!terminal && (
                <Button
                  type="button"
                  variant="ghost"
                  disabled={cancelling || uncertain}
                  onClick={() => {
                    void act("cancel");
                  }}
                >
                  {t("Cancel setup")}
                </Button>
              )}
              {(uncertain || !!error) && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    void read();
                  }}
                >
                  {t("Refresh status")}
                </Button>
              )}
              {terminal && snapshot.step.state !== "completed" && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setAttempt((value) => value + 1)}
                >
                  {t("Start again")}
                </Button>
              )}
            </div>
          </form>
        </>
      )}
      {!snapshot && !!error && (
        <Button
          variant="outline"
          onClick={() => setAttempt((value) => value + 1)}
        >
          {t("Start again")}
        </Button>
      )}
      {uncertain && (
        <p role="status" className={styles.description}>
          {t(
            "The action outcome is unknown. Refresh status before taking another action; it will not be replayed.",
          )}
        </p>
      )}
    </section>
  );
}
