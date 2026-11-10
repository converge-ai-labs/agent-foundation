import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  ArrowSquareOutIcon,
  ArrowsClockwiseIcon,
  FilePlusIcon,
  FileMinusIcon,
  PencilSimpleIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection, ModalFrame } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import { Patch } from "../../shared/diff";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { FileBody, FileBrowser, fileTree } from "../../shared/files";
import { invalidateMemories } from "./api";
import { organizationQueries } from "./organizations-api";
import styles from "./organization-review.module.css";

type Candidate = Schema["OrganizationCandidate"];
type ApplyMode = "checked" | "replace_all";

export function OrganizationCandidate({ id }: { id: string }) {
  return <CandidateReview key={id} id={id} />;
}

function CandidateReview({ id }: { id: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    ...organizationQueries(client, workspace.id).candidate(id),
    refetchOnWindowFocus: false,
    refetchOnMount: "always",
  });
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (query.isFetchedAfterMount && query.isSuccess) setLoaded(true);
  }, [query.isFetchedAfterMount, query.isSuccess]);
  const [revision, setRevision] = useState(0);
  async function reload() {
    const result = await query.refetch();
    if (result.isSuccess) setRevision((value) => value + 1);
  }
  return (
    <div className={styles.review}>
      <ErrorNotice error={query.error} retry={() => void reload()} />
      {!loaded
        ? query.isFetching && <Loading variant="detail" />
        : query.data && (
            <CandidateSnapshot
              key={`${id}:${query.data.status}:${revision}`}
              initial={query.data}
              reload={reload}
              reloading={query.isFetching}
            />
          )}
      {!query.data && !query.isPending && <p>{t("Candidate unavailable")}</p>}
    </div>
  );
}

function CandidateSnapshot({
  initial,
  reload,
  reloading,
}: {
  initial: Candidate;
  reload: () => Promise<void>;
  reloading: boolean;
}) {
  // Metadata refreshes must not move the basis of an in-progress review.
  const [candidate] = useState(initial);
  const [path, setPath] = useState(initial.changes[0]?.path ?? "");
  const [reviewed, setReviewed] = useState<Set<string>>(() => new Set());
  const [confirmation, setConfirmation] = useState<ApplyMode>();
  const client = useClient(),
    cache = useQueryClient();
  const { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const diff = useQuery(
    organizationQueries(client, workspace.id).diff(
      candidate.organization_id,
      path,
      candidate.candidate_seq,
    ),
  );
  const publish = useMutation({
    mutationFn: (mode: ApplyMode) =>
      client
        .workspace(workspace.id)
        .POST(
          "/api/v1/memory-organizations/{organization_id}/candidate/apply",
          {
            params: { path: { organization_id: candidate.organization_id } },
            body: {
              mode,
              candidate_seq: candidate.candidate_seq!,
              target_seq: candidate.target_seq!,
            },
          },
        )
        .then(data),
    onSuccess: () => {
      setConfirmation(undefined);
      void invalidateMemories(cache, workspace.id);
    },
    onError: () => setConfirmation(undefined),
  });
  const changed = candidate.target_seq !== candidate.baseline_seq;
  const ready =
    candidate.status === "ready" &&
    candidate.candidate_seq !== null &&
    candidate.target_seq !== null;
  const operations = {
    create: t("Added"),
    update: t("Modified"),
    delete: t("Deleted"),
  };
  const operationIcons = {
    create: FilePlusIcon,
    update: PencilSimpleIcon,
    delete: FileMinusIcon,
  };
  const statusLabels = {
    generating: t("Generating"),
    ready: t("Ready for review"),
    failed: t("Generation failed"),
    applied: t("Applied"),
    discarded: t("Discarded"),
    unavailable: t("Unavailable"),
  };
  const index = candidate.changes.findIndex((item) => item.path === path);
  const selected = candidate.changes[index];
  const filesUrl = `${basePath}/memories/${candidate.memory_id}`;
  const targetUrl = `${basePath}/memories/${candidate.target_memory_id}`;
  const counts = (["create", "update", "delete"] as const).map((operation) => ({
    operation,
    count: candidate.changes.filter((item) => item.operation === operation)
      .length,
  }));
  return (
    <>
      <header className={styles.heading}>
        <div>
          <div className={styles.title}>
            <h3>{t("Candidate review")}</h3>
            <StatePill
              state={candidate.status}
              label={statusLabels[candidate.status]}
            />
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("Reload review")}
          title={t("Reload review")}
          loading={reloading}
          disabled={publish.isPending}
          onClick={() => void reload()}
        >
          <ArrowsClockwiseIcon />
        </Button>
      </header>
      {candidate.status === "generating" ? (
        <p className={styles.notice} role="status">
          {t(
            "Generation continues independently. Open the run to inspect progress or cancel it.",
          )}
        </p>
      ) : candidate.status === "failed" ? (
        <p className={styles.notice} role="status">
          {t(
            "Generation failed. You can inspect these files, but they cannot be applied. Start a new task to try again.",
          )}
        </p>
      ) : candidate.status === "discarded" ? (
        <p className={styles.notice} role="status">
          {t(
            "This candidate was discarded. The target memory was not changed.",
          )}
        </p>
      ) : candidate.status === "unavailable" ? (
        <p className={styles.notice} role="status">
          {t(
            "Candidate or target files are unavailable. Reload the review to check again.",
          )}
        </p>
      ) : null}
      {candidate.application && (
        <div className={styles.notice} role="status">
          <strong>{t("Changes applied")}</strong>
          <p>
            {t("Published at")}{" "}
            <Timestamp value={candidate.application.applied_at} /> ·{" "}
            {t("Result sequence")}: {candidate.application.result_seq}
          </p>
          <Link to={targetUrl}>{t("View updated memory")}</Link>
        </div>
      )}
      {ready && (changed || publish.error) && (
        <div className={styles.warning} role="status">
          <strong>
            {t(
              publish.error
                ? "Review needs attention"
                : "Target memory has changed",
            )}
          </strong>
          <p>
            {t(
              publish.error
                ? "Application was not confirmed. Your review is preserved. Reload to check the current state before applying again."
                : "These changes were prepared from an older version. Start a new task using the current memory, or inspect the target before considering a full replacement.",
            )}
          </p>
          <Link to={targetUrl} target="_blank" rel="noreferrer">
            {t("Inspect current target")}
          </Link>
          {publish.error && <ErrorNotice error={publish.error} />}
        </div>
      )}
      <div className={styles.summary}>
        <div className={styles.counts} aria-label={t("Change summary")}>
          {counts.map(({ operation, count }) => {
            const Icon = operationIcons[operation];
            return (
              <span
                key={operation}
                data-operation={operation}
                title={operations[operation]}
                aria-label={`${operations[operation]}: ${count}`}
              >
                <Icon size={15} aria-hidden="true" /> {count}
              </span>
            );
          })}
        </div>
        <span className={styles.progress}>
          {t("{{reviewed}} of {{total}} files reviewed", {
            reviewed: reviewed.size,
            total: candidate.changes.length,
          })}
        </span>
      </div>
      {candidate.changes.length ? (
        <FileBrowser
          label={t("Changed files")}
          count={candidate.changes.length}
          nodes={fileTree(candidate.changes)}
          selected={path}
          onSelect={setPath}
          marker={(file) => {
            const operation = candidate.changes.find(
              (item) => item.path === file,
            )!.operation;
            const Icon = operationIcons[operation];
            return (
              <span
                className={styles.fileMarker}
                data-operation={operation}
                title={operations[operation]}
              >
                <Icon aria-label={operations[operation]} size={14} />
                {reviewed.has(file) && (
                  <CheckIcon aria-label={t("Reviewed")} size={12} />
                )}
              </span>
            );
          }}
        >
          <div className={styles.fileHeading}>
            <div>
              <strong title={path}>{path}</strong>
              <span>{selected && operations[selected.operation]}</span>
            </div>
            <div className={styles.navigation}>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={t(
                  reviewed.has(path) ? "Reviewed" : "Mark as reviewed",
                )}
                title={t(reviewed.has(path) ? "Reviewed" : "Mark as reviewed")}
                aria-pressed={reviewed.has(path)}
                disabled={!diff.data || diff.isError}
                onClick={() =>
                  setReviewed((current) => {
                    const next = new Set(current);
                    if (next.has(path)) next.delete(path);
                    else next.add(path);
                    return next;
                  })
                }
              >
                <CheckIcon />
              </Button>
              {selected?.operation !== "delete" && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t("Inspect candidate files")}
                  title={t("Inspect candidate files")}
                  render={
                    <Link
                      to={`${filesUrl}?file=${encodeURIComponent(path)}`}
                      target="_blank"
                      rel="noreferrer"
                    />
                  }
                >
                  <ArrowSquareOutIcon />
                </Button>
              )}
              <span>
                {index + 1} / {candidate.changes.length}
              </span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={t("Previous file")}
                title={t("Previous file")}
                disabled={index <= 0}
                onClick={() => setPath(candidate.changes[index - 1].path)}
              >
                <ArrowLeftIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={t("Next file")}
                title={t("Next file")}
                disabled={index >= candidate.changes.length - 1}
                onClick={() => setPath(candidate.changes[index + 1].path)}
              >
                <ArrowRightIcon />
              </Button>
            </div>
          </div>
          <FileBody>
            <ErrorNotice error={diff.error} retry={() => void diff.refetch()} />
            {diff.isPending ? (
              <Loading variant="code" />
            ) : (
              diff.data && (
                <>
                  <Patch
                    hunks={[diff.data.diff]}
                    expanded
                    beforeLabel={t("Before: saved baseline")}
                    afterLabel={t("After: candidate")}
                  />
                  {diff.data.truncated && (
                    <p className={styles.warning} role="status">
                      {t(
                        "Diff truncated. Inspect the complete candidate file before applying.",
                      )}
                    </p>
                  )}
                </>
              )
            )}
          </FileBody>
        </FileBrowser>
      ) : (
        <p className={styles.notice}>{t("No candidate file changes.")}</p>
      )}
      {can("write") && ready && (
        <div className={styles.applyBar}>
          <div>
            <p>
              {t("Your memory stays unchanged until you apply all changes.")}
            </p>
          </div>
          <Button
            disabled={
              changed || !!publish.error || publish.isPending || reloading
            }
            onClick={() => setConfirmation("checked")}
          >
            {t("Apply all changes")}
          </Button>
        </div>
      )}
      <DisclosureSection title={t("Review details")}>
        <dl className={styles.sequences}>
          <div>
            <dt>{t("Baseline")}</dt>
            <dd>{candidate.baseline_seq}</dd>
          </div>
          <div>
            <dt>{t("Candidate")}</dt>
            <dd>{candidate.candidate_seq ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("Target")}</dt>
            <dd>{candidate.target_seq ?? "—"}</dd>
          </div>
        </dl>
        <p className={styles.detailNote}>
          {t(
            "The diff compares the saved baseline with this candidate. Applying checks both reviewed versions; it never merges changes automatically.",
          )}
        </p>
      </DisclosureSection>
      {can("write") && (ready || candidate.status === "failed") && (
        <DisclosureSection title={t("Other actions")}>
          <div className={styles.otherActions}>
            {ready && (
              <div>
                <p>
                  {t(
                    "Replace the entire target tree with this candidate, deleting target-only files. No automatic merge.",
                  )}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!publish.error || publish.isPending || reloading}
                  onClick={() => setConfirmation("replace_all")}
                >
                  {t("Replace entire target")}
                </Button>
              </div>
            )}
            <div>
              <p>
                {t(
                  "Prevent publication of this candidate. Its files remain available for inspection.",
                )}
              </p>
              <Confirm
                title={t("Discard candidate")}
                subject={t("This candidate and all its proposed changes")}
                description={t(
                  "The target memory will not be changed. This candidate can no longer be applied.",
                )}
                trigger={t("Discard candidate")}
                triggerVariant="ghost"
                danger
                action={() =>
                  client
                    .workspace(workspace.id)
                    .POST(
                      "/api/v1/memory-organizations/{organization_id}/candidate/discard",
                      {
                        params: {
                          path: { organization_id: candidate.organization_id },
                        },
                      },
                    )
                    .then(data)
                }
                onSuccess={() => {
                  void invalidateMemories(cache, workspace.id);
                }}
              />
            </div>
          </div>
        </DisclosureSection>
      )}
      <ModalFrame
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!open) setConfirmation(undefined);
        }}
        title={t(
          confirmation === "replace_all"
            ? "Replace entire target"
            : "Apply all changes",
        )}
        closeLabel={t("Close")}
        description={t(
          confirmation === "replace_all"
            ? "All current target files will be replaced by the reviewed candidate. Target-only files will be deleted."
            : "Apply the reviewed candidate and target sequences atomically. If either changed, reload and review again.",
        )}
        footer={
          <>
            <Button
              variant="outline"
              disabled={publish.isPending}
              onClick={() => setConfirmation(undefined)}
            >
              {t("Back to review")}
            </Button>
            <Button
              variant={
                confirmation === "replace_all" ? "destructive" : "default"
              }
              loading={publish.isPending}
              onClick={() => {
                if (confirmation) publish.mutate(confirmation);
              }}
            >
              {t(
                confirmation === "replace_all"
                  ? "Replace entire target"
                  : "Apply all changes",
              )}
            </Button>
          </>
        }
      >
        <div className={styles.confirmSummary}>
          <strong>
            {t("All {{count}} changed files", {
              count: candidate.changes.length,
            })}
          </strong>
          <p>
            {t("{{reviewed}} of {{total}} files reviewed", {
              reviewed: reviewed.size,
              total: candidate.changes.length,
            })}
          </p>
          <dl className={styles.sequences}>
            <div>
              <dt>{t("Candidate")}</dt>
              <dd>{candidate.candidate_seq}</dd>
            </div>
            <div>
              <dt>{t("Target")}</dt>
              <dd>{candidate.target_seq}</dd>
            </div>
          </dl>
        </div>
      </ModalFrame>
    </>
  );
}
