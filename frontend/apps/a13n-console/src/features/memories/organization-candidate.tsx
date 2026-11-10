import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckCircleIcon,
  ArrowsClockwiseIcon,
  DotsThreeIcon,
  FilePlusIcon,
  FileMinusIcon,
  PencilSimpleIcon,
  InfoIcon,
  TrashIcon,
  ArrowsLeftRightIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import {
  FileBody,
  FileBrowser,
  FileHeader,
  FileText,
  TextReader,
  fileTree,
  isMarkdown,
} from "../../shared/files";
import { invalidateMemories } from "./api";
import { organizationQueries } from "./organizations-api";
import { ReviewDialog, type ReviewAction } from "./organization-review-dialog";
import styles from "./organization-review.module.css";

type Candidate = Schema["OrganizationCandidate"];

export function OrganizationCandidate({
  id,
  fullHeight = false,
}: {
  id: string;
  fullHeight?: boolean;
}) {
  return <CandidateReview key={id} id={id} fullHeight={fullHeight} />;
}
function CandidateReview({
  id,
  fullHeight,
}: {
  id: string;
  fullHeight: boolean;
}) {
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
    <div className={styles.review} data-full-height={fullHeight || undefined}>
      <ErrorNotice error={query.error} retry={() => void reload()} />
      {!loaded
        ? query.isFetching && <Loading variant="detail" />
        : query.data && (
            <CandidateSnapshot
              key={`${id}:${query.data.status}:${revision}`}
              initial={query.data}
              reload={reload}
              reloading={query.isFetching}
              fullHeight={fullHeight}
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
  fullHeight,
}: {
  initial: Candidate;
  reload: () => Promise<void>;
  reloading: boolean;
  fullHeight: boolean;
}) {
  // Background metadata updates never move the basis of an open review.
  const [candidate] = useState(initial);
  const [search, setSearch] = useSearchParams();
  const [allFiles, setAllFiles] = useState(() => {
    const requested = search.get("path");
    return (
      !!requested &&
      initial.files.includes(requested) &&
      !initial.changes.some((item) => item.path === requested)
    );
  });
  const [reviewed, setReviewed] = useState<Set<string>>(() => new Set());
  const [confirmation, setConfirmation] = useState<ReviewAction>();
  const [details, setDetails] = useState(false);
  const client = useClient(),
    cache = useQueryClient();
  const { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const paths = allFiles
    ? candidate.files
    : candidate.changes.map((item) => item.path);
  const requested = search.get("path");
  const path = paths.includes(requested ?? "") ? requested! : (paths[0] ?? "");
  function select(path: string) {
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("path", path);
        return next;
      },
      { replace: true },
    );
  }
  const file = useQuery(
    organizationQueries(client, workspace.id).file(
      candidate.organization_id,
      path,
      candidate.candidate_seq,
    ),
  );
  const action = useMutation({
    mutationFn: (mode: ReviewAction) =>
      mode === "discard"
        ? client
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
        : client
            .workspace(workspace.id)
            .POST(
              "/api/v1/memory-organizations/{organization_id}/candidate/apply",
              {
                params: {
                  path: { organization_id: candidate.organization_id },
                },
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
  const blocked = !!action.error || action.isPending || reloading;
  const operations = {
    create: t("Added"),
    update: t("Modified"),
    delete: t("Deleted"),
  };
  const letters = { create: "A", update: "M", delete: "D" };
  const icons = {
    create: FilePlusIcon,
    update: PencilSimpleIcon,
    delete: FileMinusIcon,
  };
  const labels = {
    generating: t("Generating"),
    ready: t("Ready for review"),
    failed: t("Generation failed"),
    applied: t("Applied"),
    discarded: t("Discarded"),
    unavailable: t("Unavailable"),
  };
  const index = candidate.changes.findIndex((item) => item.path === path);
  const selected = candidate.changes[index];
  const targetUrl = `${basePath}/memories/${candidate.target_memory_id}`;
  return (
    <>
      <header className={styles.heading}>
        <div className={styles.title}>
          <h1>{t("Candidate review")}</h1>
          <StatePill
            state={candidate.status}
            label={labels[candidate.status]}
          />
        </div>
        <div className={styles.headingActions}>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("Reload review")}
            title={t("Reload review")}
            loading={reloading}
            disabled={action.isPending}
            onClick={() => void reload()}
          >
            <ArrowsClockwiseIcon />
          </Button>
          <Menu>
            <MenuTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t("Review actions")}
                  title={t("Review actions")}
                />
              }
            >
              <DotsThreeIcon />
            </MenuTrigger>
            <MenuPopup align="end">
              <MenuItem onClick={() => setDetails(true)}>
                <InfoIcon />
                {t("Review details")}
              </MenuItem>
              {can("write") && (ready || candidate.status === "failed") && (
                <>
                  <MenuSeparator />
                  {ready && (
                    <MenuItem
                      disabled={blocked}
                      onClick={() => setConfirmation("replace_all")}
                    >
                      <ArrowsLeftRightIcon />
                      {t("Replace entire target")}
                    </MenuItem>
                  )}
                  <MenuItem
                    variant="destructive"
                    disabled={blocked}
                    onClick={() => setConfirmation("discard")}
                  >
                    <TrashIcon />
                    {t("Discard candidate")}
                  </MenuItem>
                </>
              )}
            </MenuPopup>
          </Menu>
          {can("write") && ready && (
            <Button
              disabled={changed || blocked}
              onClick={() => setConfirmation("checked")}
            >
              {t("Apply all changes")}
            </Button>
          )}
        </div>
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
      {(ready || candidate.status === "failed") &&
        (changed || action.error) && (
          <div className={styles.warning} role="status">
            <strong>
              {t(
                action.error
                  ? "Review needs attention"
                  : "Target memory has changed",
              )}
            </strong>
            <p>
              {t(
                action.error
                  ? "Application was not confirmed. Your review is preserved. Reload to check the current state before applying again."
                  : "These changes were prepared from an older version. Start a new task using the current memory, or inspect the target before considering a full replacement.",
              )}
            </p>
            <Link to={targetUrl} target="_blank" rel="noreferrer">
              {t("Inspect current target")}
            </Link>
            {action.error && <ErrorNotice error={action.error} />}
          </div>
        )}
      <div className={styles.summary}>
        <div className={styles.counts} aria-label={t("Change summary")}>
          {(["create", "update", "delete"] as const).map((operation) => {
            const Icon = icons[operation],
              count = candidate.changes.filter(
                (item) => item.operation === operation,
              ).length;
            return (
              <span
                key={operation}
                data-operation={operation}
                title={operations[operation]}
                aria-label={`${operations[operation]}: ${count}`}
              >
                <Icon size={15} aria-hidden="true" />
                {count}
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
        <SegmentedControl
          size="sm"
          label={t("File filter")}
          value={allFiles ? "all" : "changed"}
          onValueChange={(value) => setAllFiles(value === "all")}
          options={[
            { value: "changed", label: t("Changed files") },
            { value: "all", label: t("All files") },
          ]}
        />
      </div>
      {paths.length ? (
        <FileBrowser
          label={t(allFiles ? "All files" : "Changed files")}
          count={paths.length}
          nodes={fileTree(paths.map((path) => ({ path })))}
          selected={path}
          onSelect={select}
          fullHeight={fullHeight}
          initialFile={!!requested}
          marker={(path) => {
            const change = candidate.changes.find((item) => item.path === path);
            return (
              <span
                className={styles.fileMarker}
                data-operation={change?.operation}
              >
                {change && (
                  <span
                    aria-label={operations[change.operation]}
                    title={operations[change.operation]}
                  >
                    {letters[change.operation]}
                  </span>
                )}
                {reviewed.has(path) && (
                  <CheckCircleIcon aria-label={t("Reviewed")} size={14} />
                )}
              </span>
            );
          }}
        >
          <FileHeader
            path={path}
            marker={
              <span className={styles.fileOperation}>
                {selected ? operations[selected.operation] : t("Unchanged")}
              </span>
            }
            actions={
              <div className={styles.navigation}>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t(
                    reviewed.has(path) ? "Reviewed" : "Mark as reviewed",
                  )}
                  title={t(
                    reviewed.has(path) ? "Reviewed" : "Mark as reviewed",
                  )}
                  aria-pressed={reviewed.has(path)}
                  disabled={!selected || !file.data || file.isError}
                  onClick={() =>
                    setReviewed((current) => {
                      const next = new Set(current);
                      if (next.has(path)) next.delete(path);
                      else next.add(path);
                      return next;
                    })
                  }
                >
                  <CheckCircleIcon
                    weight={reviewed.has(path) ? "fill" : "regular"}
                  />
                </Button>
                <span>
                  {index >= 0
                    ? `${index + 1} / ${candidate.changes.length}`
                    : "—"}
                </span>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t("Previous file")}
                  title={t("Previous file")}
                  disabled={index <= 0}
                  onClick={() => select(candidate.changes[index - 1].path)}
                >
                  <ArrowLeftIcon />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t("Next file")}
                  title={t("Next file")}
                  disabled={
                    index >= candidate.changes.length - 1 ||
                    !candidate.changes.length
                  }
                  onClick={() => select(candidate.changes[index + 1].path)}
                >
                  <ArrowRightIcon />
                </Button>
              </div>
            }
          />
          <FileBody>
            <ErrorNotice error={file.error} retry={() => void file.refetch()} />
            {file.isPending ? (
              <Loading variant="code" />
            ) : (
              file.data && <CandidateFile key={path} file={file.data} />
            )}
          </FileBody>
        </FileBrowser>
      ) : (
        <p className={styles.notice}>{t("No candidate file changes.")}</p>
      )}
      <ModalFrame
        open={details}
        onOpenChange={setDetails}
        title={t("Review details")}
        closeLabel={t("Close")}
      >
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
      </ModalFrame>
      {confirmation && (
        <ReviewDialog
          key={confirmation}
          candidate={candidate}
          mode={confirmation}
          reviewed={reviewed.size}
          pending={action.isPending}
          close={() => setConfirmation(undefined)}
          confirm={() => action.mutate(confirmation)}
        />
      )}
    </>
  );
}

function CandidateFile({ file }: { file: Schema["CandidateFile"] }) {
  const { t } = useTranslation();
  const modified =
    file.baseline !== null &&
    file.candidate !== null &&
    file.baseline !== file.candidate;
  const [mode, setMode] = useState(modified ? "diff" : "file");
  const [side, setSide] = useState(
    file.candidate === null ? "before" : "after",
  );
  const [view, setView] = useState("source");
  const text = (side === "before" ? file.baseline : file.candidate) ?? "";
  return (
    <div className={styles.fileContent}>
      <div className={styles.readModes}>
        <SegmentedControl
          size="sm"
          label={t("Inspection mode")}
          value={mode}
          onValueChange={setMode}
          options={[
            { value: "diff", label: t("Diff") },
            { value: "file", label: t("File") },
          ]}
        />
        {mode === "file" && (
          <>
            <SegmentedControl
              size="sm"
              label={t("File version")}
              value={side}
              onValueChange={setSide}
              options={[
                {
                  value: "before",
                  label: t("Before"),
                  disabled: file.baseline === null,
                },
                {
                  value: "after",
                  label: t("After"),
                  disabled: file.candidate === null,
                },
              ]}
            />
            {isMarkdown(file.path) && (
              <SegmentedControl
                size="sm"
                label={t("Markdown view")}
                value={view}
                onValueChange={setView}
                options={[
                  { value: "source", label: t("Source") },
                  { value: "preview", label: t("Preview") },
                ]}
              />
            )}
          </>
        )}
      </div>
      {mode === "diff" ? (
        <TextReader
          path={file.path}
          before={file.baseline ?? ""}
          text={file.candidate ?? ""}
          beforeLabel={t("Before: saved baseline")}
          afterLabel={t("After: candidate")}
        />
      ) : (
        <FileText
          text={text}
          preview={isMarkdown(file.path) && view === "preview"}
        />
      )}
    </div>
  );
}
