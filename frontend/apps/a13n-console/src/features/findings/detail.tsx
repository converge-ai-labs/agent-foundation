import { SparkleIcon } from "@phosphor-icons/react";
import { Button, ChoiceField, FormField, ModalFrame, Textarea } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { ApiError } from "../../service-client";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { MarkdownContent } from "../../shared/markdown";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import {
  DetailHeader,
  DetailPage,
  Page,
  RailNote,
  RailRow,
  RailSection,
  Section,
} from "../../shared/page";
import { FormActions } from "../../shared/forms";
import { CopyableId } from "../../shared/identity";
import { useAgentComposer } from "../agents/composer";
import {
  assessmentLabels,
  categoryLabels,
  Severity,
  useFindingAgents,
} from "./page";
import styles from "./findings.module.css";

export function FindingDetail() {
  const { findingId = "" } = useParams(),
    { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    cache = useQueryClient(),
    composer = useAgentComposer(),
    agents = useFindingAgents();
  const query = useQuery({
    queryKey: ["finding", workspace.id, findingId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/findings/{finding_id}", {
          params: { path: { finding_id: findingId } },
          signal,
        })
        .then(data),
  });
  const finding = query.data;
  const revision = useQuery({
    queryKey: ["agent-revision", workspace.id, finding?.agent_revision_id],
    enabled: !!finding,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents/{agent_id}/revisions/{revision_id}", {
          params: {
            path: {
              agent_id: finding!.agent_id,
              revision_id: finding!.agent_revision_id,
            },
          },
          signal,
        })
        .then(data),
  });
  const update = useMutation({
    mutationFn: ({
      body,
      etag,
    }: {
      body: Schema["FindingUpdate"];
      etag: string;
    }) =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/findings/{finding_id}", {
          body,
          params: {
            path: { finding_id: findingId },
            header: ifMatch(etag),
          },
        })
        .then(data),
    onSuccess: (value) => {
      cache.setQueryData(["finding", workspace.id, findingId], value);
      void cache.invalidateQueries({ queryKey: ["findings", workspace.id] });
    },
    onError: () => {
      void query.refetch();
    },
  });
  if (query.isPending) return <Loading page variant="detail" />;
  if (query.error || !finding)
    return (
      <Page
        title={t("Finding")}
        back={`${basePath}/findings`}
        backLabel={t("Findings")}
      >
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      </Page>
    );
  const agent = agents.data?.find((agent) => agent.id === finding.agent_id);
  const fix = () => {
    if (!revision.data) return;
    composer.start({
      agent: { id: finding.agent_id, name: agent?.name ?? finding.agent_id },
      revision: { id: finding.agent_revision_id, number: revision.data.number },
      context: `Review finding ${finding.id}: read it (GET /api/v1/findings/{finding_id}) before proposing a change. Verify the hypothesis and cited traces against the named revision, state evidence limitations, and preserve unrelated settings.`,
    });
  };
  return (
    <DetailPage
      back={`${basePath}/findings`}
      backLabel={t("Findings")}
      header={
        <DetailHeader
          name={finding.title}
          actions={
            <>
              {can("write") && (
                <AssessmentEditor
                  key={finding.id}
                  finding={finding}
                  pending={update.isPending}
                  error={update.error}
                  onReset={() => update.reset()}
                  onSave={(body, etag) => update.mutateAsync({ body, etag })}
                />
              )}
              {can("write") && (
                <Button
                  variant="outline"
                  disabled={update.isPending}
                  onClick={() =>
                    update.mutate({
                      body: { closed: !finding.closed },
                      etag: rowTag(finding),
                    })
                  }
                >
                  {finding.closed ? t("Reopen finding") : t("Close finding")}
                </Button>
              )}
              {can("run") && composer.available && (
                <Button
                  disabled={
                    !revision.data ||
                    composer.pending ||
                    ["expected", "false_positive"].includes(finding.assessment)
                  }
                  onClick={fix}
                >
                  <SparkleIcon aria-hidden="true" />
                  {t("Review with Composer")}
                </Button>
              )}
            </>
          }
        />
      }
      rail={
        <>
          <RailSection title={t("Overview")}>
            <RailRow label={t("Severity")}>
              <Severity finding={finding} />
            </RailRow>
            <RailRow label={t("Category")}>
              {t(categoryLabels[finding.category])}
            </RailRow>
            <RailRow label={t("Assessment")}>
              {t(assessmentLabels[finding.assessment])}
            </RailRow>
            <RailRow label={t("Status")}>
              {finding.closed ? t("Closed") : t("Open")}
            </RailRow>
            <RailRow label={t("Agent")}>
              <Link
                className={styles.link}
                to={`${basePath}/agents/${finding.agent_id}`}
              >
                {agent?.name ?? finding.agent_id}
                {revision.data && ` v${revision.data.number}`}
              </Link>
            </RailRow>
            <RailRow label={t("Updated")}>
              <Timestamp value={finding.updated_at} relative />
            </RailRow>
          </RailSection>
          <RailSection title={t("Technical references")}>
            <RailRow label={t("Finding ID")}>
              <CopyableId value={finding.id} />
            </RailRow>
            <RailRow label={t("Agent version")}>
              <CopyableId value={finding.agent_revision_id} />
            </RailRow>
            {finding.analysis_id && (
              <RailRow label={t("Analysis ID")}>
                <CopyableId value={finding.analysis_id} />
              </RailRow>
            )}
            {finding.source_run_id && (
              <RailRow label={t("Analysis run")}>
                <RunEvidence runId={finding.source_run_id} />
              </RailRow>
            )}
          </RailSection>
          {!finding.source_run_id && (
            <RailNote>
              {t("Submitted through the findings API or an Agent tool.")}
            </RailNote>
          )}
        </>
      }
    >
      {composer.setup}
      <ErrorNotice error={update.error ?? composer.error ?? revision.error} />
      <Section title={t("What happened")}>
        <MarkdownContent text={finding.explanation} />
      </Section>
      <Section
        title={t("Evidence")}
        description={t(
          "Trace availability depends on backend retention and capture. External producers are responsible for their trace and span references.",
        )}
      >
        <div className={styles.evidenceList}>
          {finding.evidence.map((evidence, index) => (
            <Evidence key={index} evidence={evidence} number={index + 1} />
          ))}
        </div>
      </Section>
      <Section title={t("Suggested improvement")}>
        <MarkdownContent text={finding.suggestion} />
      </Section>
      {finding.limitations && (
        <Section title={t("Evidence limitations")}>
          <MarkdownContent text={finding.limitations} />
        </Section>
      )}
      {(finding.assessment !== "unreviewed" || finding.assessment_note) && (
        <Section title={t("Assessment")}>
          <div className={styles.reviewSummary}>
            <strong>{t(assessmentLabels[finding.assessment])}</strong>
            {finding.assessment_note && (
              <MarkdownContent text={finding.assessment_note} />
            )}
          </div>
        </Section>
      )}
    </DetailPage>
  );
}
function Evidence({
  evidence,
  number,
}: {
  evidence: Schema["Evidence"];
  number: number;
}) {
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  return (
    <div className={styles.evidenceCard}>
      <div className={styles.evidenceHeader}>
        <Link
          className={styles.evidenceTitle}
          to={`${basePath}/traces/${evidence.trace_id}`}
        >
          {t("Trace {{number}}", { number })}
        </Link>
        <RunEvidence runId={evidence.run_id} />
      </div>
      <dl className={styles.evidenceIds}>
        <div>
          <dt>{t("Trace ID")}</dt>
          <dd>
            <CopyableId value={evidence.trace_id} />
          </dd>
        </div>
        <div>
          <dt>{t("Run ID")}</dt>
          <dd>
            <CopyableId value={evidence.run_id} />
          </dd>
        </div>
        {evidence.span_ids?.length ? (
          <div>
            <dt>{t("Steps")}</dt>
            <dd>
              {evidence.span_ids.map((id) => (
                <CopyableId key={id} value={id} />
              ))}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}
/** A link to the cited run, or a quiet note when it cannot be read. */
function RunEvidence({ runId }: { runId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["run", workspace.id, runId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id: runId } },
          signal,
        })
        .then(data),
  });
  if (query.error)
    return (
      <span className={styles.unavailable}>
        {t("Run unavailable")}
        {!(query.error instanceof ApiError && query.error.status === 404) && (
          <Button
            variant="ghost"
            size="xs"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            {t("Retry")}
          </Button>
        )}
      </span>
    );
  return query.data ? (
    <Link
      className={styles.link}
      to={`${basePath}/sessions/${query.data.session_id}/threads/${query.data.thread_id}/runs/${runId}`}
    >
      {t("Open run")}
    </Link>
  ) : (
    <span className={styles.unavailable}>{t("Open run")}</span>
  );
}

function AssessmentEditor({
  finding,
  pending,
  error,
  onReset,
  onSave,
}: {
  finding: Schema["Finding"];
  pending: boolean;
  error: unknown;
  onReset: () => void;
  onSave: (body: Schema["FindingUpdate"], etag: string) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  // A background refresh must not advance the draft's concurrency precondition.
  const [baseline, setBaseline] = useState(finding);
  const [assessment, setAssessment] = useState(finding.assessment);
  const [note, setNote] = useState(finding.assessment_note);
  const [editing, setEditing] = useState(false);
  const dirty =
    assessment !== baseline.assessment || note !== baseline.assessment_note;
  const label =
    finding.assessment === "unreviewed" && !finding.assessment_note
      ? t("Add assessment")
      : t("Edit assessment");
  return (
    <ModalFrame
      open={editing}
      onOpenChange={(open) => {
        if (pending) return;
        if (open) {
          setBaseline(finding);
          setAssessment(finding.assessment);
          setNote(finding.assessment_note);
          onReset();
        }
        setEditing(open);
      }}
      trigger={
        <Button variant="outline" disabled={pending}>
          {label}
        </Button>
      }
      title={label}
      closeLabel={t("Close")}
      description={t(
        "Record your judgment of the evidence. This does not change the original diagnosis.",
      )}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (dirty && !pending) {
            void onSave(
              { assessment, assessment_note: note },
              rowTag(baseline),
            ).then(
              () => setEditing(false),
              () => {}, // The mutation error stays visible beside the retained draft.
            );
          }
        }}
      >
        <ErrorNotice error={error} />
        <ChoiceField
          label={t("Assessment")}
          value={assessment}
          disabled={pending}
          options={Object.entries(assessmentLabels).map(([value, label]) => ({
            value,
            label: t(label),
          }))}
          onValueChange={(value) => {
            if (value !== assessment) setNote("");
            setAssessment(value as Schema["Finding"]["assessment"]);
          }}
        />
        <FormField
          label={t("Review note")}
          description={t(
            "Explain your judgment. Later analyses of this Agent version will receive reviewed findings and their notes.",
          )}
        >
          <Textarea
            rows={5}
            maxLength={2048}
            value={note}
            disabled={pending}
            placeholder={t("What did the analysis miss or misunderstand?")}
            onChange={(event) => setNote(event.target.value)}
          />
        </FormField>
        <FormActions
          pending={pending}
          disabled={!dirty || pending}
          label={t("Save assessment")}
          onCancel={() => setEditing(false)}
        />
      </form>
    </ModalFrame>
  );
}
