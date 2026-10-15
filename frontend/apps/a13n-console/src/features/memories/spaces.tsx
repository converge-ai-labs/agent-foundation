import { BrainIcon, PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { Page } from "../../shared/page";
import { invalidateSpaces, spaceQueries } from "./spaces-api";

export function MemorySpacesPage() {
  const { workspace, can, basePath } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [enabled, setEnabled] = useState<boolean>();
  const term = search.trim().toLocaleLowerCase();
  const page = useCursor({ term, enabled });
  const query = useQuery(
    spaceQueries(client, workspace.id).page(term, enabled, page.cursor),
  );
  const create = can("write") ? (
    <CreateSpace
      onCreated={(id) => navigate(`${basePath}/memory-spaces/${id}`)}
    />
  ) : undefined;
  const filtered = !!term || enabled !== undefined;
  return (
    <Page
      title={t("Memory Spaces")}
      description={t(
        "Shared knowledge, session notes and automatic organization in one place.",
      )}
      actions={create}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search Spaces")}
          filters={
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={enabled === undefined ? "all" : String(enabled)}
              options={[
                { value: "all", label: t("All statuses") },
                { value: "true", label: t("Enabled") },
                { value: "false", label: t("Disabled") },
              ]}
              onValueChange={(value) =>
                setEnabled(value === "all" ? undefined : value === "true")
              }
            />
          }
          trailing={
            <Button
              variant="ghost"
              render={<Link to={`${basePath}/memories`} />}
            >
              {t("Standalone memories")}
            </Button>
          }
        />
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Memory Spaces")}
            items={query.data.items}
            onRowActivate={(space) => navigate(space.id)}
            columns={[
              {
                label: t("Space"),
                tone: "primary",
                render: (space) => (
                  <ResourceIdentity
                    to={space.id}
                    name={space.name}
                    description={space.description ?? space.id}
                    resourceId={space.id}
                    icon={<BrainIcon size={16} />}
                  />
                ),
              },
              {
                label: t("Status"),
                render: (space) => (
                  <StatePill state={space.enabled ? "enabled" : "disabled"} />
                ),
              },
              {
                label: t("Organization"),
                render: (space) =>
                  t(space.organization.enabled ? "Automatic" : "Manual"),
              },
              {
                label: t("Files"),
                render: (space) =>
                  t("{{count}} files", { count: space.file_count }),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (space) => (
                  <Timestamp value={space.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} Spaces on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<BrainIcon size={20} />}
            title={t(filtered ? "No matching Spaces" : "No Memory Spaces yet")}
            description={t(
              filtered
                ? "Change or clear the search and filters."
                : "Create a Space, then select it in an agent's defaults or a session.",
            )}
            action={!filtered && create}
          />
        )
      )}
    </Page>
  );
}

function CreateSpace({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Create Memory Space")}
      description={t(
        "Start with a name. Add organization and memory instructions in configuration.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button>
          <PlusIcon size={14} />
          {t("Create Space")}
        </Button>
      }
    >
      {open && (
        <CreateSpaceForm
          onCancel={() => setOpen(false)}
          onCreated={onCreated}
        />
      )}
    </ModalFrame>
  );
}
function CreateSpaceForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (id: string) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [name, setName] = useState(""),
    [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/memory-spaces", {
          body: { name: name.trim(), description: description.trim() || null },
        })
        .then(data),
    onSuccess: (space) => {
      void invalidateSpaces(cache, workspace.id);
      onCreated(space.id);
    },
  });
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <FormField label={t("Name")}>
        <Input
          required
          maxLength={128}
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={create.isPending}
        />
      </FormField>
      <TextAreaField
        label={t("Description (optional)")}
        value={description}
        onChange={setDescription}
        rows={3}
        disabled={create.isPending}
      />
      <ErrorNotice error={create.error} />
      <FormActions
        pending={create.isPending}
        disabled={!name.trim()}
        label={t("Create Space")}
        onCancel={onCancel}
      />
    </form>
  );
}
