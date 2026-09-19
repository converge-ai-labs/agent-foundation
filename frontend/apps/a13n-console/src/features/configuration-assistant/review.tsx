import { FileTextIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  Textarea,
} from "a13n-ui";
import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { JsonView, TextAreaField } from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import { RailRow, RailSection } from "../../shared/page";
import { Pagination, useCursor } from "../../shared/collection";
import { useConfigurationApplications, useConfigurationDraft } from "./api";
import styles from "./configuration.module.css";
import { Differences } from "./differences";
import { useAgent } from "../agents/queries";

type Review = Schema["ConfigurationDraftReview"];

function Receipt({
  receipt,
}: {
  receipt: Schema["ConfigurationApplicationReceipt"];
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace();
  const agent = useAgent(receipt.agent_id);
  return (
    <section
      className={styles.summaryCard}
      aria-label={t("Application receipt")}
    >
      <h3>{t("Application receipt")}</h3>
      <dl className={styles.summaryFields}>
        <div>
          <dt>{t("Application status")}</dt>
          <dd>
            {t(
              receipt.no_change
                ? "Applied without a configuration change"
                : "Draft applied",
            )}
          </dd>
        </div>
        <div>
          <dt>{t("Agent version")}</dt>
          <dd>
            {agent.data?.key ? (
              <Link
                to={`${basePath}/agents/${encodeURIComponent(agent.data.key)}`}
              >
                {t("Open agent")} · v{receipt.agent_revision_version}
              </Link>
            ) : (
              <span>v{receipt.agent_revision_version}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>{t("Applied at")}</dt>
          <dd>
            <Timestamp value={receipt.applied_at} />
          </dd>
        </div>
        <div>
          <dt>{t("Reviewed draft")}</dt>
          <dd>v{receipt.reviewed_version}</dd>
        </div>
        {receipt.verification_acknowledgement && (
          <>
            <div>
              <dt>{t("Execution verification")}</dt>
              <dd>{t("Execution not verified")}</dd>
            </div>
            <div>
              <dt>{t("Reason for applying without verification")}</dt>
              <dd>{receipt.verification_acknowledgement.reason}</dd>
            </div>
          </>
        )}
      </dl>
      <ErrorNotice error={agent.error} retry={() => void agent.refetch()} />
      <DisclosureSection title={t("Technical details")}>
        <dl className={styles.summaryFields}>
          <div>
            <dt>{t("Agent revision ID")}</dt>
            <dd className={styles.technicalValue}>
              {receipt.agent_revision_id}
            </dd>
          </div>
          <div>
            <dt>{t("Reviewed content digest (SHA-256)")}</dt>
            <dd className={styles.technicalValue}>{receipt.reviewed_digest}</dd>
          </div>
        </dl>
      </DisclosureSection>
    </section>
  );
}

function ApplicationHistory({ draftId }: { draftId: string }) {
  const { t } = useTranslation(),
    page = useCursor();
  const history = useConfigurationApplications(draftId, page.cursor);
  return (
    <DisclosureSection title={t("Application history")}>
      {history.isPending && <Loading variant="list" />}
      <ErrorNotice error={history.error} retry={() => void history.refetch()} />
      {history.data?.items.map((receipt) => (
        <Receipt key={receipt.reviewed_version} receipt={receipt} />
      ))}
      {history.data?.items.length === 0 && (
        <p className={styles.reviewNote}>{t("No applications yet")}</p>
      )}
      <Pagination page={page} next={history.data?.next_cursor} />
    </DisclosureSection>
  );
}

export function DraftReview({ draftId }: { draftId: string }) {
  const query = useConfigurationDraft(draftId),
    { t } = useTranslation();
  if (query.isPending)
    return (
      <aside className={styles.review}>
        <Loading variant="form" />
      </aside>
    );
  if (!query.data)
    return (
      <aside className={styles.review}>
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      </aside>
    );
  const draft = query.data.value;
  const actions = draft.status === "open" && (
    <DraftActions
      draft={draft}
      etag={query.data.etag}
      refresh={async () => {
        await query.refetch();
      }}
    />
  );
  return (
    <aside
      className={`${styles.review} a13n-scrollbar`}
      aria-label={t("Configuration draft")}
    >
      <header className={styles.reviewHeader}>
        <h2>
          {t("Draft")} · v{draft.version}
        </h2>
        <StatePill state={draft.status} />
      </header>
      <p className={styles.reviewNote}>
        {t(
          "Saving a draft does not change your agent. Apply only after reviewing the candidate and its differences.",
        )}
      </p>
      {draft.target_conflict && (
        <p role="alert" className={styles.notice}>
          {t(
            "The target agent changed. Review both changes and explicitly rebase before applying.",
          )}
        </p>
      )}
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {draft.config ? (
        <>
          <RailSection>
            {draft.creation_metadata && (
              <RailRow label={t("Agent name")}>
                <span>{draft.creation_metadata.name}</span>
              </RailRow>
            )}
            <RailRow label={t("Model")}>
              <span title={draft.config.model.model_key}>
                {draft.config.model.model_key}
              </span>
            </RailRow>
            <RailRow label={t("Source revision")}>
              <span>
                {draft.source ? `v${draft.source.version}` : t("Empty")}
              </span>
            </RailRow>
            <RailRow label={t("Base version")}>
              <span>{draft.base ? `v${draft.base.version}` : "—"}</span>
            </RailRow>
            <RailRow label={t("Current target")}>
              <span>
                {draft.current_target
                  ? `v${draft.current_target.version}`
                  : "—"}
              </span>
            </RailRow>
            <RailRow label={t("Validation")}>
              {draft.latest_validation ? (
                <Timestamp
                  value={draft.latest_validation.checked_at}
                  relative
                />
              ) : (
                <span>{t("Not validated")}</span>
              )}
            </RailRow>
          </RailSection>
          {actions}
          <DisclosureSection title={t("Instructions")} defaultOpen>
            <p className={styles.instructions}>
              {draft.config.instructions || "—"}
            </p>
          </DisclosureSection>
          <Differences
            title={t("Current target to candidate")}
            changes={draft.current_target_to_candidate}
          />
          <Differences
            title={t("Source to candidate")}
            changes={draft.source_to_candidate}
          />
          {draft.target_conflict && (
            <>
              <Differences
                title={t("Base to candidate")}
                changes={draft.base_to_candidate}
              />
              <Differences
                title={t("Base to current target")}
                changes={draft.base_to_current_target}
              />
            </>
          )}
          <DisclosureSection title={t("Complete configuration")}>
            <JsonView value={draft.config} />
          </DisclosureSection>
        </>
      ) : (
        <>
          <div className={styles.draftEmpty}>
            <FileTextIcon size={24} aria-hidden="true" />
            <h3>{t("Your draft will take shape here")}</h3>
            <p>
              {t(
                "Describe your agent in the conversation. Review its configuration here before applying it.",
              )}
            </p>
          </div>
          {actions}
        </>
      )}
      {draft.latest_validation && (
        <section
          className={styles.summaryCard}
          aria-label={t("Configuration validation")}
        >
          <h3>{t("Configuration validation")}</h3>
          <dl className={styles.summaryFields}>
            <div>
              <dt>{t("Validation status")}</dt>
              <dd>{t("Configuration validated")}</dd>
            </div>
            <div>
              <dt>{t("Checked at")}</dt>
              <dd>
                <Timestamp value={draft.latest_validation.checked_at} />
              </dd>
            </div>
            <div>
              <dt>{t("Validation scope")}</dt>
              <dd>
                {t(
                  "Validation checks configuration and dependencies. It does not prove execution succeeded.",
                )}
              </dd>
            </div>
          </dl>
          {!!draft.latest_validation.warnings?.length && (
            <div>
              <h4>{t("Warnings")}</h4>
              <ul>
                {draft.latest_validation.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      {draft.latest_application_receipt && (
        <Receipt receipt={draft.latest_application_receipt} />
      )}
      <ApplicationHistory draftId={draft.id} />
      <DisclosureSection title={t("Draft details")}>
        <dl className={styles.summaryFields}>
          <div>
            <dt>{t("Draft")}</dt>
            <dd className={styles.technicalValue}>{draft.id}</dd>
          </div>
          <div>
            <dt>{t("Content digest (SHA-256)")}</dt>
            <dd className={styles.technicalValue}>{draft.content_digest}</dd>
          </div>
        </dl>
      </DisclosureSection>
    </aside>
  );
}

function DraftActions({
  draft,
  etag,
  refresh,
}: {
  draft: Review;
  etag?: string;
  refresh: () => Promise<void>;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient();
  const saveKey = useIdempotency(),
    applyKey = useIdempotency(),
    discardKey = useIdempotency();
  const [editing, setEditing] = useState<{ draft: Review; etag?: string }>(),
    [reviewed, setReviewed] = useState<Review>(),
    [reviewEtag, setReviewEtag] = useState<string>();
  const [reason, setReason] = useState("");
  const [changeSummary, setChangeSummary] = useState("");
  const header = (key: string, tag = etag) => {
    if (!tag) throw new Error(t("Reload the draft before making changes."));
    return { ...commandHeaders(workspace.id, key), "If-Match": tag };
  };
  const apply = useMutation({
    mutationFn: () => {
      if (!reviewed?.latest_validation)
        throw new Error(t("Validate the candidate before applying."));
      const body: Schema["ApplyDraftRequest"] = {
        expected_version: reviewed.version,
        content_digest: reviewed.content_digest,
        dependency_digest: reviewed.latest_validation.dependency_digest,
        change_summary: changeSummary.trim() || null,
        verification_acknowledgement: {
          outcome: "unverified",
          reason: reason.trim(),
        },
      };
      return client.http
        .POST("/api/v1/configuration-drafts/{draft_id}/apply", {
          params: {
            path: { draft_id: reviewed.id },
            header: header(applyKey.forBody(body), reviewEtag),
          },
          body,
        })
        .then(data);
    },
    onSuccess: async () => {
      applyKey.reset();
      setReviewed(undefined);
      setReason("");
      setChangeSummary("");
      await refresh();
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["agents", workspace.id] }),
        cache.invalidateQueries({
          queryKey: ["configuration-thread", workspace.id],
        }),
        cache.invalidateQueries({
          queryKey: ["configuration-applications", workspace.id, draft.id],
        }),
      ]);
    },
  });
  const save = useMutation({
    mutationFn: (body: Schema["UpdateConfigurationDraftRequest"]) =>
      client.http
        .PATCH("/api/v1/configuration-drafts/{draft_id}", {
          params: {
            path: { draft_id: draft.id },
            header: header(saveKey.forBody(body), editing?.etag ?? etag),
          },
          body,
        })
        .then(data),
    onSuccess: async () => {
      saveKey.reset();
      setEditing(undefined);
      await refresh();
    },
  });
  const discard = useMutation({
    mutationFn: () => {
      const body = { expected_version: draft.version };
      return client.http
        .POST("/api/v1/configuration-drafts/{draft_id}/discard", {
          params: {
            path: { draft_id: draft.id },
            header: header(discardKey.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: async () => {
      discardKey.reset();
      await refresh();
    },
  });
  const allowed = can(
    draft.mode === "create" ? "agent.create" : "agent.revision.create",
  );
  const applyLabel = t(
    draft.mode === "create" ? "Create agent" : "Apply reviewed configuration",
  );
  return (
    <>
      <ErrorNotice error={save.error ?? discard.error} retry={refresh} />
      <div className={styles.actions}>
        <Button
          variant="default"
          disabled={
            !allowed || !draft.latest_validation || draft.target_conflict
          }
          onClick={() => {
            setReviewed(draft);
            setChangeSummary(draft.suggested_change_summary ?? "");
            setReviewEtag(etag);
            apply.reset();
          }}
        >
          {t("Review and apply")}
        </Button>
        <Button
          variant="outline"
          loading={save.isPending}
          disabled={!draft.config || !allowed}
          onClick={() =>
            save.mutate({
              expected_version: draft.version,
              expected_digest: draft.content_digest,
              operations: [{ op: "set", path: [], value: draft.config }],
            })
          }
        >
          {t("Validate")}
        </Button>
        <Button
          variant="outline"
          disabled={!allowed}
          onClick={() => setEditing({ draft, etag })}
        >
          {t("Edit draft")}
        </Button>
        <Button
          variant="ghost"
          disabled={!allowed}
          loading={discard.isPending}
          onClick={() => discard.mutate()}
        >
          {t("Discard")}
        </Button>
      </div>
      <ModalFrame
        open={!!editing}
        onOpenChange={(open) => {
          if (!open && !save.isPending) setEditing(undefined);
        }}
        title={t("Edit configuration draft")}
        description={t(
          "Edit the candidate configuration directly. Saving replaces the draft; it does not change your agent.",
        )}
        closeLabel={t("Close")}
        size="lg"
      >
        {editing && (
          <DraftEditor
            draft={editing.draft}
            etag={editing.etag}
            saved={async () => {
              setEditing(undefined);
              await refresh();
            }}
            save={(body) => save.mutateAsync(body)}
          />
        )}
      </ModalFrame>
      <ModalFrame
        open={!!reviewed}
        onOpenChange={(open) => {
          if (!open && !apply.isPending) setReviewed(undefined);
        }}
        title={t("Apply reviewed draft")}
        description={t(
          "These are the exact changes that become a new agent version.",
        )}
        closeLabel={t("Close")}
        size="lg"
        footer={
          reviewed && (
            <>
              <Button
                variant="outline"
                disabled={apply.isPending}
                onClick={() => setReviewed(undefined)}
              >
                {t("Cancel")}
              </Button>
              <Button
                disabled={!reason.trim()}
                loading={apply.isPending}
                onClick={() => apply.mutate()}
              >
                {applyLabel}
              </Button>
            </>
          )
        }
      >
        {reviewed && (
          <div className={styles.applyForm}>
            <p className={styles.reviewNote}>
              {t("Draft version")} v{reviewed.version} ·{" "}
              <span className={styles.technicalValue}>
                {reviewed.content_digest}
              </span>
            </p>
            <Differences
              title={t("Changes to apply")}
              changes={reviewed.current_target_to_candidate}
            />
            <TextAreaField
              label={t("Version note")}
              value={changeSummary}
              rows={2}
              hint={t("Shown in the agent's version history.")}
              onChange={setChangeSummary}
            />
            <TextAreaField
              label={t("Reason for applying without verification")}
              value={reason}
              rows={2}
              hint={t(
                "This candidate has not been execution-verified. Explain why you are applying it without verification.",
              )}
              onChange={setReason}
            />
            <ErrorNotice error={apply.error} />
          </div>
        )}
      </ModalFrame>
    </>
  );
}

function DraftEditor({
  draft,
  etag,
  saved,
  save,
}: {
  draft: Review;
  etag?: string;
  saved: () => Promise<void>;
  save: (body: Schema["UpdateConfigurationDraftRequest"]) => Promise<unknown>;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    key = useIdempotency();
  const [text, setText] = useState(JSON.stringify(draft.config ?? {}, null, 2)),
    [name, setName] = useState(draft.creation_metadata?.name ?? "");
  const [description, setDescription] = useState(
    draft.creation_metadata?.description ?? "",
  );
  const mutation = useMutation({
    mutationFn: async (rebase: boolean) => {
      const config = JSON.parse(text) as Schema["AgentConfig-Input"];
      if (rebase) {
        if (!draft.current_target || !draft.current_target_etag || !etag)
          throw new Error(t("Reload the draft before making changes."));
        const body = {
          config,
          expected_version: draft.version,
          expected_target_etag: draft.current_target_etag,
        };
        data(
          await client.http.POST(
            "/api/v1/configuration-drafts/{draft_id}/rebase",
            {
              params: {
                path: { draft_id: draft.id },
                header: {
                  ...commandHeaders(workspace.id, key.forBody(body)),
                  "If-Match": etag,
                },
              },
              body,
            },
          ),
        );
        key.reset();
        await saved();
      } else
        await save({
          expected_version: draft.version,
          expected_digest: draft.content_digest,
          operations: [{ op: "set", path: [], value: config }],
          ...(draft.mode === "create"
            ? { creation_metadata: { name, description: description || null } }
            : {}),
        });
    },
  });
  return (
    <div className={styles.editorForm}>
      {draft.mode === "create" && (
        <>
          <FormField label={t("Agent name")}>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </FormField>
          <TextAreaField
            label={t("Description")}
            value={description}
            rows={2}
            onChange={setDescription}
          />
        </>
      )}
      <TextAreaField
        label={t("Complete configuration JSON")}
        code
        rows={16}
        value={text}
        onChange={setText}
      />
      <ErrorNotice error={mutation.error} />
      <div className={styles.editorActions} data-a13n-form-actions>
        {draft.target_conflict && (
          <Button
            variant="outline"
            loading={mutation.isPending}
            onClick={() => mutation.mutate(true)}
          >
            {t("Use edited candidate and rebase")}
          </Button>
        )}
        <Button
          loading={mutation.isPending}
          onClick={() => mutation.mutate(false)}
        >
          {t("Save draft")}
        </Button>
      </div>
    </div>
  );
}
