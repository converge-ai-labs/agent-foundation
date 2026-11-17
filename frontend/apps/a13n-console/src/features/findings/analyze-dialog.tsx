import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
  Button,
  Checkbox,
  ChoiceField,
  DisclosureSection,
  FormField,
  Label,
  ModalFrame,
  SearchPicker,
  SegmentedControl,
} from "a13n-ui";
import { ArrowCounterClockwiseIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { CopyableId } from "../../shared/identity";
import { DateTimeField, FormSection, TextAreaField } from "../../shared/forms";
import {
  formatLocalDateTime,
  parseLocalDateTime,
} from "../../shared/local-date-time";
import { useMountedModal } from "../../shared/dialogs";
import { useIdempotency } from "../../shared/idempotency";
import { ApiError } from "../../service-client";
import { AgentAvatar } from "../agents/avatar";
import { useTraceBackend } from "../traces/backend";
import { SessionScopePicker } from "./session-scope";
import { rules, useFindingAgents } from "./shared";
import styles from "./findings.module.css";

const ranges = {
  all: "All available session traces",
  "1": "Last 24 hours",
  "7": "Last 7 days",
  "30": "Last 30 days",
  custom: "Custom range",
} as const;

/**
 * Starts one managed analysis. The request key belongs to the submitted body,
 * so an uncertain acceptance can be recovered by resending that exact body.
 */
export function AnalyzeDialog({
  traceId,
  onClose: onClosed,
  onCreated,
}: {
  traceId?: string;
  onClose: () => void;
  onCreated: (analysis: Schema["Analysis"]) => void;
}) {
  const formId = useId();
  const [created, setCreated] = useState<Schema["Analysis"] | null>(null);
  const { close: onClose, modalProps } = useMountedModal(() => {
    if (created) onCreated(created);
    else onClosed();
  });
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath } = useWorkspace(),
    backend = useTraceBackend();
  const agents = useFindingAgents();
  const [scope, setScope] = useState("workspace"),
    [agent, setAgent] = useState(""),
    [sessions, setSessions] = useState(new Map<string, string>());
  const [days, setDays] = useState<keyof typeof ranges>("1"),
    [instructions, setInstructions] = useState(""),
    [presets, setPresets] = useState<Schema["AnalysisCreate"]["presets"]>([
      "execution",
      "recovery",
      "answer",
    ]);
  const command = useIdempotency();
  const [windowEnd] = useState(() => new Date().toISOString());
  const [start, setStart] = useState(() =>
      formatLocalDateTime(new Date(Date.parse(windowEnd) - 86400000)),
    ),
    [end, setEnd] = useState(() => formatLocalDateTime(new Date(windowEnd)));
  const custom = days === "custom";
  const startDate = parseLocalDateTime(start),
    endDate = parseLocalDateTime(end);
  const invalidWindow =
    custom &&
    (!startDate ||
      !endDate ||
      endDate <= startDate ||
      endDate.getTime() - startDate.getTime() > 31 * 86400000);
  const body: Schema["AnalysisCreate"] = {
    trace_id: traceId,
    ...(scope === "agent" ? { agent_id: agent } : {}),
    session_ids: scope === "sessions" ? Array.from(sessions.keys()) : undefined,
    started_after:
      traceId || days === "all"
        ? undefined
        : custom
          ? startDate?.toISOString()
          : new Date(
              Date.parse(windowEnd) - Number(days) * 86400000,
            ).toISOString(),
    started_before: traceId
      ? undefined
      : custom
        ? endDate?.toISOString()
        : windowEnd,
    max_traces: traceId
      ? 1
      : scope === "sessions"
        ? Math.max(10, sessions.size)
        : 10,
    presets,
    instructions,
  };
  const mutation = useMutation({
    mutationFn: (request: Schema["AnalysisCreate"]) =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/finding-analyses", {
          body: request,
          params: { header: commandHeaders(command.forBody(request)) },
        })
        .then(data),
    onSuccess: (analysis) => {
      void cache.invalidateQueries({
        queryKey: ["finding-analyses", workspace.id],
      });
      void cache.invalidateQueries({ queryKey: ["agents", workspace.id] });
      setCreated(analysis);
      onClose();
    },
  });
  const unconfirmed =
    mutation.isError &&
    (!(mutation.error instanceof ApiError) || mutation.error.status >= 500);
  const changedRequest =
    unconfirmed && JSON.stringify(body) !== JSON.stringify(mutation.variables);
  const disabled =
    mutation.isPending ||
    invalidWindow ||
    (scope === "sessions" && !sessions.size) ||
    (scope === "agent" && !agent) ||
    !presets?.length ||
    !backend.data?.type;
  const checks = presets?.length ?? 0;
  const summary = traceId
    ? t("1 trace · {{checks}} of 3 checks", { checks })
    : t("Up to {{limit}} traces · {{range}} · {{checks}} of 3 checks", {
        limit: body.max_traces,
        range: t(ranges[days]),
        checks,
      });
  return (
    <ModalFrame
      size="xl"
      {...modalProps}
      title={t("Analyze traces")}
      closeLabel={t("Close")}
      description={t(
        "Find potential issues for your review. Your agents stay unchanged.",
      )}
      footer={
        <>
          <span className={styles.requestSummary}>{summary}</span>
          <Button
            type="button"
            variant="outline"
            disabled={mutation.isPending}
            onClick={onClose}
          >
            {t("Cancel")}
          </Button>
          <Button type="submit" form={formId} disabled={disabled}>
            {mutation.isPending
              ? t("Starting…")
              : changedRequest
                ? t("Start new analysis")
                : t("Start analysis")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        onSubmit={(event) => {
          event.preventDefault();
          if (!disabled) mutation.mutate(body);
        }}
        className={styles.form}
      >
        <ErrorNotice
          error={backend.error ?? (!unconfirmed && mutation.error)}
        />
        {unconfirmed && mutation.variables && (
          <Alert variant="warning" role="status">
            <ArrowCounterClockwiseIcon aria-hidden="true" />
            <AlertTitle>{t("Start not confirmed")}</AlertTitle>
            <AlertDescription>
              <p>
                {t(
                  "The analysis may already have started. Retry the previous request to recover it. Edited settings start a new analysis.",
                )}
              </p>
            </AlertDescription>
            <AlertAction>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={mutation.isPending}
                onClick={() => mutation.mutate(mutation.variables!)}
              >
                {t("Retry previous request")}
              </Button>
            </AlertAction>
          </Alert>
        )}
        {backend.isSuccess && !backend.data.type && (
          <p role="alert" className={styles.blocked}>
            {t("Connect a trace provider to start analysis.")}
          </p>
        )}
        <FormSection title={t("Scope")}>
          <div className={styles.scopeRows}>
            {traceId ? (
              <FormField label={t("Selected trace")}>
                <span className={styles.selectedTrace}>
                  <CopyableId value={traceId} />
                  <Link
                    className={styles.link}
                    to={`${basePath}/traces/${traceId}`}
                  >
                    {t("Open trace")}
                  </Link>
                </span>
              </FormField>
            ) : (
              <>
                <FormField label={t("Analyze")}>
                  <SegmentedControl
                    label={t("Analyze")}
                    size="default"
                    value={scope}
                    options={[
                      {
                        value: "workspace",
                        label: t("Workspace"),
                        disabled: mutation.isPending,
                      },
                      {
                        value: "agent",
                        label: t("Agent"),
                        disabled: mutation.isPending,
                      },
                      {
                        value: "sessions",
                        label: t("Sessions"),
                        disabled: mutation.isPending,
                      },
                    ]}
                    onValueChange={(value) => {
                      setScope(value);
                      setDays(value === "sessions" ? "all" : "1");
                    }}
                  />
                </FormField>
                {scope === "agent" && (
                  <>
                    <ErrorNotice
                      error={agents.error}
                      retry={() => void agents.refetch()}
                    />
                    <FormField label={t("Agent")}>
                      <SearchPicker
                        label={t("Agent")}
                        placeholder={t("Select agent")}
                        emptyMessage={t("No matching agents")}
                        value={agent || undefined}
                        onValueChange={setAgent}
                        disabled={mutation.isPending}
                        groups={[
                          {
                            label: t("Agents"),
                            options: (agents.data ?? [])
                              .filter((item) => item.preset_kind !== "finding")
                              .map((item) => ({
                                value: item.id,
                                label: item.name,
                                icon: (
                                  <AgentAvatar
                                    name={item.name}
                                    id={item.id}
                                    url={item.image_url}
                                    className="size-6 shrink-0"
                                  />
                                ),
                              })),
                          },
                        ]}
                      />
                    </FormField>
                  </>
                )}
                {scope === "sessions" && (
                  <SessionScopePicker
                    selected={sessions}
                    onChange={setSessions}
                    disabled={mutation.isPending}
                  />
                )}
                <ChoiceField
                  label={t("Time range")}
                  value={days}
                  options={(Object.keys(ranges) as (keyof typeof ranges)[])
                    .filter((key) => key !== "all" || scope === "sessions")
                    .map((key) => ({ value: key, label: t(ranges[key]) }))}
                  onValueChange={(value) =>
                    setDays(value as keyof typeof ranges)
                  }
                  disabled={mutation.isPending}
                />
                {custom && (
                  <>
                    <DateTimeField
                      label={t("Start time")}
                      value={start}
                      onValueChange={setStart}
                      disabled={mutation.isPending}
                    />
                    <DateTimeField
                      label={t("End time")}
                      value={end}
                      onValueChange={setEnd}
                      disabled={mutation.isPending}
                    />
                    {invalidWindow && (
                      <p role="alert" className={styles.fieldError}>
                        {t("Choose a time range of up to 31 days.")}
                      </p>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        </FormSection>
        <fieldset className={styles.rules} disabled={mutation.isPending}>
          <legend>{t("Checks")}</legend>
          <div className={styles.ruleList}>
            {rules.map((rule) => (
              <Label key={rule.key} className={styles.rule}>
                <Checkbox
                  checked={presets?.includes(rule.key) ?? false}
                  onCheckedChange={(checked) =>
                    setPresets((previous) =>
                      checked
                        ? [...(previous ?? []), rule.key]
                        : previous?.filter((key) => key !== rule.key),
                    )
                  }
                />
                <span>
                  {t(rule.label)}
                  <span className={styles.hint}>{t(rule.description)}</span>
                </span>
              </Label>
            ))}
          </div>
        </fieldset>
        <TextAreaField
          label={t("Requirements for this analysis")}
          hint={t(
            "Optional. Applies only to this analysis; saved supplemental instructions stay unchanged.",
          )}
          value={instructions}
          onChange={setInstructions}
          maxLength={8192}
          disabled={mutation.isPending}
          rows={4}
        />
        <DisclosureSection title={t("How analysis works")}>
          <ul className={styles.analysisNotes}>
            <li>
              {t(
                "Analysis maintains the required tools while preserving your model, supported model settings and supplemental instructions.",
              )}
            </li>
            {!traceId && (
              <li>
                {t(
                  "Failure and recovery checks prioritize traces with errors. Findings still need your review.",
                )}
              </li>
            )}
            <li>
              {t(
                "Previous findings and reviews for the same agent version help avoid duplicates. Context may be shortened.",
              )}
            </li>
            {!traceId && (
              <li>
                {t(
                  "Each analysis scans up to 100 traces and selects up to 20.",
                )}
              </li>
            )}
          </ul>
        </DisclosureSection>
      </form>
    </ModalFrame>
  );
}
