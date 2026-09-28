import { PlusIcon, WarningIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AlertDescription,
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  SegmentedControl,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import shared from "../../shared/shared.module.css";
import { ManageProvidersLink } from "../providers";
import { invalidateMemories, memoryProviders } from "./api";
import { AlwaysLoadField, GuideField, useMemoryTypeName } from "./fields";
import { memoryCreate, memoryDraft } from "./form";
import styles from "./memories.module.css";

type Kind = Schema["MemoryKind"];

export function CreateMemory({
  onCreated,
}: {
  onCreated: (memory: Schema["Memory"]) => void;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="default" type="button">
          <PlusIcon size={14} />
          {t("Create memory")}
        </Button>
      }
      size="lg"
      title={t("Create memory")}
      description={t(
        "A memory keeps what agents read and write across conversations.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open && (
        <CreateForm
          onCancel={() => setOpen(false)}
          onCreated={(memory) => {
            setOpen(false);
            onCreated(memory);
          }}
        />
      )}
    </ModalFrame>
  );
}

function CreateForm({
  onCreated,
  onCancel,
}: {
  onCreated: (memory: Schema["Memory"]) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [draft, setDraft] = useState(memoryDraft);
  const [kind, setKind] = useState<Kind>("file");
  const [providerId, setProviderId] = useState("");
  const [namespace, setNamespace] = useState("");
  const typeName = useMemoryTypeName();
  const record = kind === "record";
  // Only an enabled provider the workspace may use keeps new records.
  const providers = useQuery({
    ...memoryProviders(client, workspace.id),
    enabled: record,
    select: (items) => items.filter((item) => item.enabled),
  });
  const provider = providers.data?.find((item) => item.id === providerId);
  const create = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/memories", {
          body: memoryCreate(
            draft,
            record && provider ? { provider, namespace } : undefined,
          ),
        })
        .then(data),
    onSuccess: (memory) => {
      void invalidateMemories(cache, workspace.id);
      onCreated(memory);
    },
  });
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <div className={styles.kind}>
        <SegmentedControl
          label={t("Kind")}
          value={kind}
          onValueChange={(value) => setKind(value as Kind)}
          options={[
            { value: "file", label: t("File memory") },
            { value: "record", label: t("Record memory") },
          ]}
        />
        <p className={styles.note}>
          {t(
            record
              ? "Short records in a memory provider, such as mem0. Each run recalls the records closest to its input."
              : "A small tree of text files the Service stores, with the history of every change.",
          )}
        </p>
      </div>
      <FormField label={t("Name")} disabled={create.isPending}>
        <Input
          required
          value={draft.name}
          maxLength={128}
          onChange={(event) => {
            setDraft({ ...draft, name: event.target.value });
          }}
        />
      </FormField>
      <TextAreaField
        label={t("Description")}
        rows={2}
        value={draft.description}
        onChange={(description) => setDraft({ ...draft, description })}
      />
      {record && (
        <>
          <ErrorNotice
            error={providers.error}
            retry={() => void providers.refetch()}
          />
          {providers.data && !providers.data.length ? (
            <div className={styles.missingProvider}>
              <p className={styles.note}>
                {t(
                  "No memory provider is enabled for this workspace. Add one in provider settings first.",
                )}
              </p>
              <ManageProvidersLink category="memory" />
            </div>
          ) : (
            <ChoiceField
              label={t("Memory provider")}
              description={t(
                "The backend account that keeps the records. It cannot change later.",
              )}
              placeholder={
                providers.isPending ? t("Loading…") : t("Select provider")
              }
              required
              value={providerId}
              onValueChange={setProviderId}
              options={(providers.data ?? []).map((item) => ({
                value: item.id,
                label: `${item.name} · ${typeName(item.type)}`,
              }))}
            />
          )}
          <DisclosureSection
            title={t("Existing namespace")}
            summary={namespace.trim() || t("New namespace")}
          >
            <FormField
              label={t("Namespace")}
              description={t(
                "Leave empty to give the memory a new namespace. Name one the provider already holds, such as a mem0 user_id, to adopt its records. It cannot change later.",
              )}
              disabled={create.isPending}
            >
              <Input
                value={namespace}
                maxLength={256}
                pattern="[^\s*]+"
                autoComplete="off"
                onChange={(event) => setNamespace(event.target.value)}
              />
            </FormField>
            {namespace.trim() && (
              <Alert variant="warning">
                <WarningIcon aria-hidden="true" />
                <AlertDescription>
                  {t(
                    "Deleting this memory deletes every record in {{namespace}} from the provider, including the records it adopts.",
                    { namespace: namespace.trim() },
                  )}
                </AlertDescription>
              </Alert>
            )}
          </DisclosureSection>
        </>
      )}
      <GuideField
        kind={kind}
        value={draft.guide}
        onChange={(guide) => setDraft({ ...draft, guide })}
      />
      {!record && (
        <div className="grid gap-2">
          <span className="text-[13px] font-medium">
            {t("Always loaded files")}
          </span>
          <AlwaysLoadField
            value={draft.alwaysLoad}
            onChange={(alwaysLoad) => setDraft({ ...draft, alwaysLoad })}
          />
        </div>
      )}
      <ErrorNotice error={create.error} />
      <FormActions
        onCancel={onCancel}
        pending={create.isPending}
        disabled={record && !provider}
        label={t("Create memory")}
      />
    </form>
  );
}
