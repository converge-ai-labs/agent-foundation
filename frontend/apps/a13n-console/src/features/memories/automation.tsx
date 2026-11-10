import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ifMatch, representation, type Schema } from "../../shared/api";
import { ConflictNotice } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { Section } from "../../shared/page";
import { invalidateMemories, isStale } from "./api";
import {
  policyDraft,
  policyValue,
  validPolicy,
  type PolicyDraft,
} from "./organization-form";
import { OrganizationPolicyFields } from "./organization-policy-fields";
import { MemoryOrganizations } from "./organizations";
import { organizationQueries } from "./organizations-api";
import { MemoryRunLink } from "./run-link";

type Resource = { value: Schema["MemoryAutomation"]; etag?: string };
export function MemoryAutomation({ memory }: { memory: Schema["Memory"] }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery(
    organizationQueries(client, workspace.id).automation(memory.id),
  );
  return (
    <div className="grid gap-9">
      <MemoryOrganizations
        target={{ kind: "memory", id: memory.id }}
        model={query.data?.value.policy.model ?? undefined}
      />
      <Section
        title={t("Automatic organization")}
        description={t(
          "Curate saved file changes and explicitly selected conversation history in bounded background rounds.",
        )}
      >
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        {query.isPending ? (
          <Loading variant="form" />
        ) : (
          query.data && <AutomationPolicy resource={query.data} />
        )}
      </Section>
    </div>
  );
}
function AutomationPolicy({ resource }: { resource: Resource }) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const [edit, setEdit] = useState<{ base: Resource; draft: PolicyDraft }>();
  const base = edit?.base ?? resource,
    draft = edit?.draft ?? policyDraft(base.value.policy),
    state = resource.value.status;
  const save = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .PUT("/api/v1/memories/{memory_id}/organization", {
          params: { path: { memory_id: base.value.id } },
          headers: ifMatch(base.etag),
          body: policyValue(draft),
        })
        .then(representation),
    onSuccess: (saved) => {
      cache.setQueryData(
        organizationQueries(client, workspace.id).automation(saved.value.id)
          .queryKey,
        saved,
      );
      setEdit(undefined);
      void invalidateMemories(cache, workspace.id);
    },
  });
  const dirty =
    !!edit &&
    JSON.stringify(policyValue(draft)) !==
      JSON.stringify(policyValue(policyDraft(base.value.policy)));
  return (
    <div className="grid max-w-[680px] gap-5">
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <StatePill
          state={
            state.active_run_id
              ? "running"
              : state.pending
                ? "pending"
                : state.failure
                  ? "failed"
                  : "ready"
          }
        />
        <span>
          {t(
            resource.value.policy.enabled
              ? "Automatic organization is on"
              : "Manual organization",
          )}
        </span>
        <span>
          {t("Consumed source runs")}: {resource.value.processed_history_runs}
        </span>
      </div>
      {state.failure && (
        <p role="alert" className="text-xs">
          {state.failure.message}
        </p>
      )}
      {state.history_gap && (
        <p role="status" className="text-xs">
          {t(
            "Some revision history is no longer available, so progress could not be confirmed. Review the files or organize manually.",
          )}
        </p>
      )}
      {state.active_run_id && (
        <MemoryRunLink runId={state.active_run_id} explainUnavailable />
      )}
      {state.pending && state.due_at && (
        <p className="text-xs">
          {t("Scheduled")}: <Timestamp value={state.due_at} />
        </p>
      )}
      <DisclosureSection
        title={t("Automatic policy")}
        summary={resource.value.policy.model ?? t("Choose a model…")}
      >
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (dirty && validPolicy(draft) && !save.isPending) save.mutate();
          }}
        >
          <fieldset className="fieldset-reset" disabled={save.isPending}>
            <OrganizationPolicyFields
              draft={draft}
              change={(next) => setEdit({ base, draft: { ...draft, ...next } })}
              readOnly={!can("write")}
            />
          </fieldset>
          {isStale(save.error) ? (
            <ConflictNotice
              title={t("Policy changed")}
              description={t(
                "Your draft is preserved. Reload the current policy to discard it and review the saved version.",
              )}
              recover={{
                label: t("Reload current policy"),
                onClick: () => {
                  setEdit(undefined);
                  save.reset();
                  void invalidateMemories(cache, workspace.id);
                },
              }}
            />
          ) : (
            <ErrorNotice error={save.error} />
          )}
          {dirty && can("write") && (
            <FormActions
              onCancel={() => {
                setEdit(undefined);
                save.reset();
              }}
              pending={save.isPending}
              disabled={!validPolicy(draft)}
              label={t("Save policy")}
            />
          )}
        </form>
      </DisclosureSection>
    </div>
  );
}
