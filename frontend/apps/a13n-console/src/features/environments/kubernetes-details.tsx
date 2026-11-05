import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice, Timestamp } from "../../shared/feedback";
import { TextAreaField, SwitchField } from "../../shared/forms";
import { CopyableId } from "../../shared/identity";
import { Fact } from "./fact";
import styles from "./environments.module.css";

export function KubernetesDetails({
  environment,
  etag,
}: {
  environment: Schema["EnvironmentView"];
  etag?: string;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    { t } = useTranslation();
  const [inspecting, setInspecting] = useState(false);
  const [container, setContainer] = useState("");
  const [previous, setPrevious] = useState(false);
  const http = client.workspace(environment.workspace_id);
  const params = { path: { environment_id: environment.id } };
  const inspect = useQuery({
    queryKey: [
      "kubernetes-diagnostics",
      environment.workspace_id,
      environment.id,
    ],
    enabled: inspecting && can("run"),
    queryFn: ({ signal }) =>
      http
        .GET("/api/v1/environments/{environment_id}/kubernetes/diagnostics", {
          params,
          signal,
        })
        .then(data),
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const logs = useMutation({
    gcTime: 0,
    mutationFn: () =>
      http
        .GET("/api/v1/environments/{environment_id}/kubernetes/logs", {
          params: {
            ...params,
            query: {
              container: container || undefined,
              previous,
              tail_lines: 100,
            },
          },
        })
        .then(data),
  });
  const hold = useMutation({
    mutationFn: (seconds: number | null) =>
      http
        .PUT("/api/v1/environments/{environment_id}/debug-hold", {
          params,
          headers: ifMatch(etag),
          body: { seconds },
        })
        .then(data),
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["environment", environment.id],
      });
    },
  });
  const native = inspect.data?.native;
  const live = !["deleted", "deleting"].includes(environment.status);
  return (
    <>
      <section className={styles.factGroup}>
        <h3>{t("Debug hold")}</h3>
        <p className={styles.panelNote}>
          {t(
            "Postpones automatic cleanup only. Explicit deletion and Kubernetes eviction still apply. Inspection does not extend the hold.",
          )}
        </p>
        {environment.debug_hold_until && (
          <Timestamp value={environment.debug_hold_until} />
        )}
        {can("write") && live && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={hold.isPending || !etag}
              onClick={() => hold.mutate(3600)}
            >
              {t("Hold for 1 hour")}
            </Button>
            {environment.debug_hold_until && (
              <Button
                size="sm"
                variant="ghost"
                disabled={hold.isPending || !etag}
                onClick={() => hold.mutate(null)}
              >
                {t("Release hold")}
              </Button>
            )}
          </div>
        )}
        <ErrorNotice error={hold.error} />
      </section>
      {can("run") && environment.status !== "deleted" && (
        <section className={styles.factGroup}>
          <h3>{t("Kubernetes diagnostics")}</h3>
          <p className={styles.panelNote}>
            {t(
              "Read native status without opening an execution session or keeping the instance alive.",
            )}
          </p>
          <Button
            size="sm"
            variant="outline"
            disabled={inspect.isFetching}
            onClick={() => {
              if (inspecting) void inspect.refetch();
              else setInspecting(true);
            }}
          >
            {t(
              inspect.isFetching
                ? "Loading…"
                : inspecting
                  ? "Refresh diagnostics"
                  : "Inspect Kubernetes",
            )}
          </Button>
          <ErrorNotice error={inspect.error} />
          {native && (
            <>
              <dl className={styles.facts}>
                <Fact label={t("Observed")}>
                  <Timestamp value={native.observed_at} />
                </Fact>
                <Fact label={t("Namespace")}>{native.namespace}</Fact>
                <Fact label={t("Pod")}>
                  <CopyableId value={native.pod_name} />
                </Fact>
                <Fact label={t("Pod UID")}>
                  {native.pod_uid ? (
                    <CopyableId value={native.pod_uid} />
                  ) : (
                    t("Not assigned")
                  )}
                </Fact>
                <Fact label={t("Stage")}>{native.stage}</Fact>
                <Fact label={t("Connection")}>{native.connection}</Fact>
                <Fact label="Envd">
                  {t(
                    native.envd === "previously_ready"
                      ? "Previously verified; not a live probe"
                      : "Not verified",
                  )}
                </Fact>
              </dl>
              {native.errors.map((error, index) => (
                <p key={index} role="status">
                  {error}
                </p>
              ))}
              <DisclosureSection title={t("Native resources")}>
                {native.resources.map((item) => (
                  <div
                    key={`${item.reference.kind}/${item.reference.name}`}
                    className={styles.factGroup}
                  >
                    <strong>
                      {item.reference.kind} · {item.reference.name}
                    </strong>
                    <p>
                      {item.observation} · {item.reference.disposition}
                      {item.reason ? ` · ${item.reason}` : ""}
                    </p>
                    {item.reference.uid && (
                      <CopyableId value={item.reference.uid} />
                    )}
                    {item.deleting_at && (
                      <p>
                        {t("Deleting since")}{" "}
                        <Timestamp value={item.deleting_at} />
                      </p>
                    )}
                    {!!item.finalizers?.length && (
                      <p>
                        {t("Finalizers")}: {item.finalizers?.join(", ")}
                      </p>
                    )}
                    {Object.keys(item.status ?? {}).length > 0 && (
                      <TextAreaField
                        label={t("Native status")}
                        value={JSON.stringify(item.status, null, 2)}
                        onChange={() => {}}
                        readOnly
                        code
                        rows={8}
                      />
                    )}
                  </div>
                ))}
              </DisclosureSection>
              <DisclosureSection title={t("Kubernetes events")}>
                {!native.events.length && <p>{t("No events returned")}</p>}
                {native.events.map((event, index) => (
                  <div key={index} className={styles.factGroup}>
                    <strong>{event.reason ?? event.type}</strong>
                    <p>{event.message}</p>
                    {event.last_timestamp && (
                      <Timestamp value={event.last_timestamp} />
                    )}
                  </div>
                ))}
                {native.events_truncated && (
                  <p>
                    {t("Events were truncated; inspect with kubectl for more.")}
                  </p>
                )}
              </DisclosureSection>
              <DisclosureSection title={t("Rendered configuration")}>
                <TextAreaField
                  label={t("Redacted native configuration")}
                  value={JSON.stringify(native.rendered, null, 2)}
                  onChange={() => {}}
                  readOnly
                  code
                  rows={12}
                />
              </DisclosureSection>
              <TextAreaField
                label={t("kubectl commands")}
                value={native.commands.join("\n")}
                onChange={() => {}}
                readOnly
                code
                rows={3}
              />
              <DisclosureSection title={t("Container logs")}>
                <FormField
                  label={t("Container name (optional)")}
                  description={t("Empty selects the execution container.")}
                >
                  <Input
                    value={container}
                    onChange={(event) => setContainer(event.target.value)}
                  />
                </FormField>
                <SwitchField
                  label={t("Previous container")}
                  checked={previous}
                  onCheckedChange={setPrevious}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={logs.isPending}
                  onClick={() => logs.mutate()}
                >
                  {t("Read last 100 lines")}
                </Button>
                <ErrorNotice error={logs.error} />
                {logs.data && (
                  <TextAreaField
                    label={`${logs.data.container} · ${logs.data.pod_uid}`}
                    value={logs.data.text}
                    readOnly
                    onChange={() => {}}
                    code
                    rows={12}
                    hint={t(
                      "Output is limited to 64 KiB. Guest logs may contain application secrets.",
                    )}
                  />
                )}
              </DisclosureSection>
            </>
          )}
        </section>
      )}
      {environment.retirement_state && (
        <section className={styles.factGroup}>
          <DisclosureSection title={t("Retained resource receipt")}>
            <TextAreaField
              label={t("Retirement state")}
              value={JSON.stringify(environment.retirement_state, null, 2)}
              onChange={() => {}}
              readOnly
              code
              rows={10}
            />
          </DisclosureSection>
        </section>
      )}
    </>
  );
}
