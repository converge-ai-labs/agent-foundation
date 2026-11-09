import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import { Patch } from "../../shared/diff";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { invalidateMemories } from "./api";
import { organizationQueries } from "./organizations-api";

export function OrganizationCandidate({ id }: { id: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    ...organizationQueries(client, workspace.id).candidate(id),
    refetchOnWindowFocus: false,
  });
  const [revision, setRevision] = useState(0);
  return (
    <div className="grid gap-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-[15px] font-medium">{t("Candidate review")}</h3>
        <Button
          variant="outline"
          size="sm"
          loading={query.isFetching}
          onClick={async () => {
            const result = await query.refetch();
            if (result.isSuccess) setRevision((value) => value + 1);
          }}
        >
          {t("Reload review")}
        </Button>
      </div>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="detail" />
      ) : (
        query.data && (
          <CandidateSnapshot
            key={`${id}:${query.data.status}:${revision}`}
            initial={query.data}
          />
        )
      )}
    </div>
  );
}
function CandidateSnapshot({
  initial,
}: {
  initial: Schema["OrganizationCandidate"];
}) {
  const [candidate] = useState(initial),
    [path, setPath] = useState(initial.changes[0]?.path ?? ""),
    [mode, setMode] = useState<"checked" | "replace_all">("checked");
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery(
    organizationQueries(client, workspace.id).diff(
      candidate.organization_id,
      path,
      candidate.candidate_seq,
    ),
  );
  const refresh = () => {
    void invalidateMemories(cache, workspace.id);
  };
  const statusLabels = {
    generating: t("Generating"),
    ready: t("Ready for review"),
    failed: t("Generation failed"),
    applied: t("Applied"),
    discarded: t("Discarded"),
    unavailable: t("Unavailable"),
  };
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
        <strong className="font-medium text-foreground">
          {statusLabels[candidate.status]}
        </strong>
        <span>
          {t("Baseline")}: {candidate.baseline_seq}
        </span>
        <span>
          {t("Candidate")}: {candidate.candidate_seq ?? "—"}
        </span>
        <span>
          {t("Target")}: {candidate.target_seq ?? "—"}
        </span>
        <Link to={`${basePath}/memories/${candidate.memory_id}`}>
          {t("Inspect candidate files")}
        </Link>
      </div>
      {candidate.status === "generating" && (
        <p className="text-xs text-muted-foreground">
          {t(
            "Generation continues independently. Open the run to inspect progress or cancel it.",
          )}
        </p>
      )}
      {candidate.changes.length > 0 ? (
        <>
          <ChoiceField
            label={t("Changed file")}
            value={path}
            options={candidate.changes.map((item) => ({
              value: item.path,
              label: `${item.path} · ${t(item.operation)}`,
            }))}
            onValueChange={setPath}
          />
          <ErrorNotice error={query.error} retry={() => void query.refetch()} />
          {query.isPending ? (
            <Loading variant="code" />
          ) : (
            query.data && (
              <>
                <Patch hunks={[query.data.diff]} />
                {query.data.truncated && (
                  <p role="status" className="text-xs">
                    {t(
                      "Diff truncated. Inspect the complete candidate file before applying.",
                    )}
                  </p>
                )}
              </>
            )
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">
          {t("No candidate file changes.")}
        </p>
      )}
      {candidate.application && (
        <p className="text-xs text-muted-foreground">
          {t("Published at")}{" "}
          <Timestamp value={candidate.application.applied_at} /> ·{" "}
          {t("Result sequence")}: {candidate.application.result_seq}
        </p>
      )}
      {can("write") &&
        candidate.status === "ready" &&
        candidate.candidate_seq !== null &&
        candidate.target_seq !== null && (
          <>
            <ChoiceField
              label={t("Publication mode")}
              value={mode}
              options={[
                { value: "checked", label: t("Checked publication") },
                { value: "replace_all", label: t("Replace entire target") },
              ]}
              onValueChange={(value) => setMode(value as typeof mode)}
              description={t(
                mode === "checked"
                  ? "Publish only if the target still matches the acceptance baseline. No automatic merge."
                  : "Replace the entire target tree with this candidate, deleting target-only files. No automatic merge.",
              )}
            />
            <div className="flex flex-wrap gap-2">
              <Confirm
                key={`${candidate.candidate_seq}:${candidate.target_seq}:${mode}`}
                title={t(
                  mode === "checked"
                    ? "Apply candidate"
                    : "Replace entire target",
                )}
                subject={candidate.target_memory_id}
                description={t(
                  mode === "checked"
                    ? "Apply the reviewed candidate and target sequences atomically. If either changed, reload and review again."
                    : "All current target files will be replaced by the reviewed candidate. Target-only files will be deleted.",
                )}
                danger={mode === "replace_all"}
                trigger={t("Apply candidate")}
                action={() =>
                  client
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
                    .then(data)
                }
                onSuccess={refresh}
              />
            </div>
          </>
        )}
      {can("write") &&
        (candidate.status === "ready" || candidate.status === "failed") && (
          <div>
            <Confirm
              title={t("Discard candidate")}
              subject={candidate.memory_id}
              description={t(
                "Prevent publication of this candidate. Its files remain available for inspection.",
              )}
              trigger={t("Discard candidate")}
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
              onSuccess={refresh}
            />
          </div>
        )}
    </div>
  );
}
