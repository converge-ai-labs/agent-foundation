import { useEffect, useId, useRef, useState } from "react";
import {
  Button,
  ModalFrame,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { ConfirmAction } from "../shell/confirm-action";
import { SchemaForm } from "./form";
import { McpAuthorization } from "../configuration/mcp-authorization";
import {
  ConnectorIcon,
  compactSetupAction,
  setupActionIcon,
} from "./presentation";
import {
  clearSecrets,
  defaultValue,
  type FormSchema,
  type Json,
} from "./schema";
import styles from "./connectors.module.css";

function stepValues(step: Schema<"Step">): Json {
  const form = step.form as FormSchema | null;
  return form
    ? {
        ...(defaultValue(form) as Record<string, Json>),
        ...(step.values as Record<string, Json>),
      }
    : {};
}

export function ConnectorSetup({
  connectorId,
  name,
  declaration,
  intent = "configure",
  onClose,
  onComplete = onClose,
}: {
  connectorId: string;
  name?: string;
  declaration?: Schema<"Declaration"> | null;
  intent?: string;
  onClose: () => void;
  onComplete?: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const formId = useId();
  const heading = useRef<HTMLSpanElement>(null);
  const formElement = useRef<HTMLFormElement>(null);
  const [snapshot, setSnapshot] = useState<Schema<"SetupSnapshot"> | null>(
    null,
  );
  const [values, setValues] = useState<Json>({});
  const [dirty, setDirty] = useState(false);
  const [discard, setDiscard] = useState<"close" | "cancel" | null>(null);
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
  const submitActions =
    snapshot?.step.actions?.filter(
      (action) => action.kind === "submit" || action.kind === "confirm",
    ) ?? [];
  const primary = submitActions.at(-1);

  useEffect(() => {
    let active = true;
    mounted.current = true;
    const current = ++generation.current;
    setError(null);
    setValues({});
    setDirty(false);
    setUncertain(false);
    setSnapshot(null);
    void result(
      client.POST("/api/connectors/{connector_id}/setup", {
        params: { path: { connector_id: connectorId } },
        body: { intent },
      }),
    )
      .then((next) => {
        if (!active || current !== generation.current) return;
        setSnapshot(next);
        setValues(stepValues(next.step));
      })
      .catch((failure) => {
        if (active) setError(failure);
      });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, [client, connectorId, intent, attempt]);

  useEffect(() => {
    if (!snapshot) return;
    const first = formElement.current?.querySelector<HTMLInputElement>(
      'input:not([disabled]):not([type="hidden"]),textarea:not([disabled])',
    );
    if (first && !first.value) first.focus();
    else heading.current?.focus();
  }, [snapshot?.step.key]);

  useEffect(() => {
    if (snapshot?.step.state === "completed" && !busy) onComplete();
  }, [snapshot?.step.state, busy, onComplete]);

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
      await cache.invalidateQueries({ queryKey: ["connectors"] });
      if (!mounted.current || current !== generation.current) return;
      setSnapshot(next);
      setUncertain(false);
      setError(null);
      if (next.step.key !== snapshot.step.key) {
        setValues(stepValues(next.step));
        setDirty(false);
      }
    } catch (failure) {
      if (mounted.current && current === generation.current) setError(failure);
    } finally {
      reading.current = false;
    }
  }
  useEffect(() => {
    const wait = snapshot?.step.wait;
    if (!wait || terminal || busy || uncertain) return;
    const remaining =
      Math.min(Date.parse(wait.expires_at), Date.parse(snapshot.expires_at)) -
      Date.now();
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
    setDirty(false);
    // Private values stay in component/request memory, never Query caches or browser storage.
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
      if (next.step.key !== snapshot.step.key) setValues(stepValues(next.step));
      await cache.invalidateQueries({ queryKey: ["connectors"] });
    } catch (failure) {
      if (mounted.current && current === generation.current) {
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
  function requestClose() {
    if (dirty) setDiscard("close");
    else onClose();
  }
  const retry = (
    <Button
      type="button"
      variant="outline"
      disabled={busy}
      onClick={() => setAttempt((value) => value + 1)}
    >
      {t("Start again")}
    </Button>
  );
  return (
    <>
      <ModalFrame
        bottomStickOnMobile
        open
        keepVisibleWhenNested
        title={
          <span tabIndex={-1} ref={heading} className={styles.identity}>
            <ConnectorIcon declaration={declaration} />
            <span>
              {snapshot
                ? t(snapshot.step.title)
                : t("Opening connection setup…")}
            </span>
          </span>
        }
        description={name}
        closeLabel={t("Close connection setup")}
        onOpenChange={(open, details) => {
          if (!open) {
            details.cancel();
            if (!busy) requestClose();
          }
        }}
        footer={
          <div className={styles.setupFooter}>
            {terminal || !snapshot ? (
              <Button variant="outline" disabled={busy} onClick={requestClose}>
                {t("Close")}
              </Button>
            ) : (
              <Button
                type="button"
                variant="outline"
                disabled={cancelling || uncertain}
                onClick={() => {
                  if (dirty) setDiscard("cancel");
                  else void act("cancel");
                }}
              >
                {t(cancelling ? "Cancelling…" : "Cancel setup")}
              </Button>
            )}
            {(uncertain || !!error) && snapshot && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  void read();
                }}
              >
                {t("Refresh status")}
              </Button>
            )}
            {((terminal && snapshot.step.state !== "completed") ||
              (!snapshot && !!error)) &&
              retry}
            {!terminal &&
              !uncertain &&
              submitActions.map((action) => {
                const Icon = setupActionIcon(action);
                const compact = compactSetupAction(
                  action,
                  action.id === primary?.id,
                );
                const button = (
                  <Button
                    key={action.id}
                    type={action.kind === "confirm" ? "button" : "submit"}
                    form={formId}
                    value={action.id}
                    variant={action.id === primary?.id ? "default" : "outline"}
                    size={compact ? "icon" : "default"}
                    aria-label={compact ? t(action.label) : undefined}
                    disabled={busy}
                  >
                    {Icon && <Icon size={16} aria-hidden="true" />}
                    {!compact && t(action.label)}
                  </Button>
                );
                return action.kind === "confirm" ? (
                  <ConfirmAction
                    key={action.id}
                    trigger={button}
                    title={t(action.label)}
                    description={t("Continue with this action?")}
                    confirmLabel={t(action.label)}
                    onConfirm={() => {
                      void act(action.id);
                    }}
                  />
                ) : compact ? (
                  <Tooltip key={action.id}>
                    <TooltipTrigger render={button} />
                    <TooltipPopup>{t(action.label)}</TooltipPopup>
                  </Tooltip>
                ) : (
                  button
                );
              })}
          </div>
        }
      >
        <section className={styles.setup} aria-label={t("Connection setup")}>
          {uncertain && (
            <p role="status" className={styles.warning}>
              {t(
                "The action outcome is unknown. Refresh status before taking another action; it will not be replayed.",
              )}
            </p>
          )}
          <ErrorNotice error={error} />
          {snapshot && (
            <>
              <p className={styles.description} role="status">
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
              {!terminal &&
                snapshot.step.actions?.some(
                  (action) => action.kind === "oauth",
                ) && (
                  <McpAuthorization
                    target={{ kind: "connector", id: connectorId }}
                    onConnected={() => {
                      void read();
                    }}
                  />
                )}
              <form
                id={formId}
                ref={formElement}
                onSubmit={(event) => {
                  event.preventDefault();
                  const action =
                    (event.nativeEvent as SubmitEvent).submitter?.getAttribute(
                      "value",
                    ) ?? (primary?.kind === "submit" ? primary.id : undefined);
                  if (action && !busy && !terminal) void act(action);
                }}
              >
                {schema && !terminal && (
                  <SchemaForm
                    schema={schema}
                    value={values}
                    onChange={(next) => {
                      setValues(next);
                      setDirty(true);
                    }}
                    disabled={busy || uncertain}
                  />
                )}
              </form>
              <div className={styles.actions}>
                {!terminal &&
                  snapshot.step.actions
                    ?.filter(
                      (action) =>
                        action.kind === "open_url" || action.kind === "copy",
                    )
                    .map((action) => {
                      const Icon = setupActionIcon(action);
                      return action.kind === "open_url" ? (
                        <Button
                          key={action.id}
                          variant="link"
                          size="sm"
                          render={
                            <a
                              href={action.url ?? undefined}
                              target="_blank"
                              rel="noreferrer"
                            />
                          }
                        >
                          {Icon && <Icon size={14} aria-hidden="true" />}
                          {t(action.label)}
                        </Button>
                      ) : (
                        <Button
                          key={action.id}
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            void navigator.clipboard.writeText(
                              action.value ?? "",
                            );
                          }}
                        >
                          {Icon && <Icon size={14} aria-hidden="true" />}
                          {t(action.label)}
                        </Button>
                      );
                    })}
              </div>
            </>
          )}
        </section>
      </ModalFrame>
      <ModalFrame
        bottomStickOnMobile
        open={discard !== null}
        title={t("Discard unsaved changes?")}
        description={t(
          "Private values and unsaved changes will be discarded. Saved configuration is kept.",
        )}
        closeLabel={t("Close confirmation")}
        onOpenChange={(open) => {
          if (!open) setDiscard(null);
        }}
        footer={
          <>
            <Button variant="outline" onClick={() => setDiscard(null)}>
              {t("Keep editing")}
            </Button>
            <Button
              onClick={() => {
                const action = discard;
                setDiscard(null);
                if (action === "cancel") void act("cancel");
                else onClose();
              }}
            >
              {t("Discard changes")}
            </Button>
          </>
        }
      />
    </>
  );
}
