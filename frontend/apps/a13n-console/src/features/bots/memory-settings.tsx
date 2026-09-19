import { refreshMemory } from "./memory-actions";
import type { BotAccount } from "./account";
import type { MemoryDialogControl } from "./memory-actions";
import { Button, ChoiceField, Label, ModalFrame, Switch } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { MemoryProviderEditor } from "../memory/editor";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import shared from "../../shared/shared.module.css";
import styles from "./bots.module.css";

export function MemorySettings({
  account,
  reload,
  setup = false,
  onConfigured,
}: {
  account: BotAccount;
  reload?: () => Promise<void>;
  setup?: boolean;
  onConfigured?: () => void;
}) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation();
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Bot memory storage and controls")}
      closeLabel={t("Close")}
      size="md"
      trigger={
        <Button
          variant={setup ? "default" : "outline"}
          onClick={() => setOpen(true)}
        >
          {t(setup ? "Set up memory" : "Memory settings")}
        </Button>
      }
    >
      {open && (
        <SettingsForm
          account={account}
          close={() => setOpen(false)}
          reload={reload}
          onConfigured={onConfigured}
        />
      )}
    </ModalFrame>
  );
}

function SettingsForm({
  account,
  close,
  reload,
  onConfigured,
}: {
  account: BotAccount;
  close: () => void;
  reload?: () => Promise<void>;
  onConfigured?: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const [enabled, setEnabled] = useState(!!account.memory);
  const [addingProvider, setAddingProvider] = useState(false);
  const addTrigger = useRef<HTMLButtonElement>(null);
  const [providerId, setProviderId] = useState(
    account.memory?.provider_id ?? "",
  );
  const [organize, setOrganize] = useState(
    account.memory?.auto_organize ?? false,
  );
  const [useMemory, setUseMemory] = useState(
    account.memory?.use_memory ?? true,
  );
  const [saveMemory, setSaveMemory] = useState(
    account.memory?.save_on_request ?? true,
  );
  const [timezone] = useState(
    account.memory?.timezone ??
      Intl.DateTimeFormat().resolvedOptions().timeZone,
  );
  const providers = useQuery({
    queryKey: ["bot-memory-provider-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/memory-providers", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const types = useQuery({
    queryKey: ["bot-memory-provider-types", workspace.id],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/memory-provider-types", { signal }).then(data),
  });
  const support = new Map(
    (types.data?.items ?? []).map((item) => [
      item.type,
      item.supports_documents,
    ]),
  );
  const unavailable =
    enabled &&
    !(providers.data ?? []).some(
      (item) =>
        item.id === providerId &&
        item.enabled &&
        support.get(item.type) === true,
    );
  const save = useMutation({
    mutationFn: async () => {
      if (unavailable)
        throw new Error(t("Choose a Provider that supports document memory."));
      await client.http
        .PUT("/api/v1/application-accounts/{account_id}/bot/memory-settings", {
          params: { path: { account_id: account.id } },
          body: {
            expected_version: account.memoryVersion,
            memory: !enabled
              ? null
              : {
                  provider_id: providerId,
                  use_memory: useMemory,
                  save_on_request: saveMemory,
                  timezone,
                  auto_organize: organize,
                },
          },
        })
        .then(data);
      await cache.invalidateQueries({ queryKey: ["application-accounts"] });
      await reload?.();
      close();
      if (enabled && !account.memory) onConfigured?.();
    },
  });
  return (
    <form
      className={shared.stack}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <p>
        {t(
          "Only workspace administrators can view and manage connected private-group memory.",
        )}
      </p>
      <Label>
        <Switch checked={enabled} onCheckedChange={setEnabled} />
        {t("Enable memory")}
      </Label>
      <div className={styles.groupMemoryCapabilities} data-disabled={!enabled}>
        <Label>
          <Switch
            checked={useMemory}
            onCheckedChange={(value) => {
              setUseMemory(value);
              if (!value) setOrganize(false);
            }}
            disabled={!enabled}
          />
          {t("Refer to memory when answering")}
        </Label>
        <Label>
          <Switch
            checked={saveMemory}
            onCheckedChange={(value) => {
              setSaveMemory(value);
              if (!value) setOrganize(false);
            }}
            disabled={!enabled}
          />
          {t("Allow saving or deleting memory through chat")}
        </Label>
      </div>
      <p>
        {t(
          "These permissions apply to all groups. Reading and writing are independent; each group must also enable the corresponding permission.",
        )}
      </p>
      {!enabled && (
        <p>
          {t(
            "Memory is off for all groups. Existing memories are not deleted.",
          )}
        </p>
      )}
      {enabled && (
        <>
          <ErrorNotice
            error={providers.error ?? types.error}
            retry={() => {
              void providers.refetch();
              void types.refetch();
            }}
          />
          {providers.isPending || types.isPending ? (
            <Loading />
          ) : (
            <>
              <ChoiceField
                label={t("Memory storage")}
                value={providerId}
                onValueChange={(value) => {
                  setProviderId(value);
                  setOrganize(false);
                }}
                options={[
                  {
                    value: "",
                    label: t("Select memory storage"),
                    disabled: true,
                  },
                  ...(providers.data ?? []).map((item) => ({
                    value: item.id,
                    label: `${item.name}${!item.enabled ? ` · ${t("Disabled")}` : support.get(item.type) === false ? ` · ${t("Document memory unsupported")}` : support.get(item.type) !== true ? ` · ${t("Unavailable")}` : ""}`,
                    disabled: !item.enabled || support.get(item.type) !== true,
                  })),
                ]}
              />
              {!providers.error &&
                !types.error &&
                !(providers.data ?? []).some(
                  (item) => item.enabled && support.get(item.type) === true,
                ) && (
                  <p role="note">
                    {t(
                      "No compatible memory storage is available. Add storage to continue.",
                    )}
                  </p>
                )}
              {providerId && unavailable && (
                <p role="note">
                  {t("Choose a Provider that supports document memory.")}
                </p>
              )}
              {can("memory_provider.manage") && (
                <Button
                  type="button"
                  variant="outline"
                  ref={addTrigger}
                  onClick={() => setAddingProvider(true)}
                >
                  {t("Add memory storage")}
                </Button>
              )}
              {!can("memory_provider.manage") && (
                <p>
                  {t("Ask a workspace administrator to add memory storage.")}
                </p>
              )}
            </>
          )}
        </>
      )}
      {enabled &&
        providers.data?.find((item) => item.id === providerId)?.type ===
          "a13n.filesystem" && (
          <Label>
            <Switch
              checked={organize}
              onCheckedChange={setOrganize}
              disabled={!useMemory || !saveMemory}
            />
            {t("Automatic organization")}
          </Label>
        )}
      <MemoryProviderEditor
        scope={{ kind: "workspace", id: workspace.id }}
        controlledOpen={addingProvider}
        onClose={() => setAddingProvider(false)}
        finalFocus={addTrigger}
        onSaved={(provider) => {
          setProviderId(provider.id);
          void providers.refetch();
        }}
      />
      {account.memory &&
        (!enabled || providerId !== account.memory.provider_id) && (
          <p role="note">
            {t(
              "Existing documents stay on the previous Provider. This does not migrate or delete them.",
            )}
          </p>
        )}
      {enabled && providerId && !unavailable && (
        <p>
          {t(
            "Groups inherit this Provider. Configure each group separately; sharing is off by default.",
          )}
        </p>
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        disabled={
          unavailable ||
          (enabled &&
            (providers.isPending ||
              types.isPending ||
              !!providers.error ||
              !!types.error))
        }
        onCancel={close}
      />
    </form>
  );
}

export function GroupMemorySettings({
  account,
  target,
  initialScope,
  dialog,
}: {
  account: BotAccount;
  target?: Schema["AccountTarget"];
  initialScope?: Schema["Scope"];
  dialog?: MemoryDialogControl;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      {...dialog}
      title={t("Configure group memory")}
      closeLabel={t("Close")}
      size="md"
      trigger={
        dialog ? undefined : (
          <Button variant="outline" onClick={() => setOpen(true)}>
            {t("Configure group")}
          </Button>
        )
      }
    >
      {(dialog?.open ?? open) && (
        <GroupForm
          account={account}
          target={target}
          initialScope={initialScope}
          close={() => (dialog ? dialog.onOpenChange(false) : setOpen(false))}
        />
      )}
    </ModalFrame>
  );
}

function GroupForm({
  account,
  close,
  target,
  initialScope,
}: {
  account: BotAccount;
  target?: Schema["AccountTarget"];
  initialScope?: Schema["Scope"];
  close: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [group, setGroup] = useState(
      target?.external_target_id ??
        initialScope?.external_conversation_id ??
        "",
    ),
    [enabled, setEnabled] = useState(true),
    [read, setRead] = useState(true),
    [write, setWrite] = useState(true);
  const [timezone, setTimezone] = useState(account.memory?.timezone ?? "UTC");
  const [visibility, setVisibility] = useState<"group" | "installation">(
    "group",
  );
  const [organize, setOrganize] = useState(
    initialScope?.auto_organize ?? false,
  );
  const [loaded, setLoaded] = useState(false);
  const [expectedVersion, setExpectedVersion] = useState<number | undefined>();
  const choices = useQuery({
    queryKey: [
      "bot-memory-configure-options",
      account.id,
      account.memory?.provider_id,
      target?.id,
    ],
    queryFn: async ({ signal }) => {
      if (target) {
        const scopes = data(
          await client.http.GET(
            "/api/v1/application-accounts/{account_id}/memory-scopes",
            {
              params: {
                path: { account_id: account.id },
                query: {
                  provider_id: account.memory!.provider_id,
                  target_id: target.id,
                  limit: 1,
                },
              },
              signal,
            },
          ),
        );
        return { targets: [target], scopes: scopes.items };
      }
      const [targets, scopes] = await Promise.all([
        allPages((cursor) =>
          client.http
            .GET("/api/v1/application-accounts/{account_id}/targets", {
              params: {
                path: { account_id: account.id },
                query: { cursor, limit: 100 },
              },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/application-accounts/{account_id}/memory-scopes", {
              params: {
                path: { account_id: account.id },
                query: {
                  provider_id: account.memory!.provider_id,
                  cursor,
                  limit: 100,
                },
              },
              signal,
            })
            .then(data),
        ),
      ]);
      return { targets, scopes };
    },
  });
  const scope = choices.data?.scopes.find(
    (item) => item.external_conversation_id === group,
  );
  if ((target || initialScope) && choices.data && !loaded) {
    setLoaded(true);
    setEnabled(scope?.enabled ?? true);
    setVisibility(scope?.visibility ?? "group");
    setRead(scope?.use_memory ?? true);
    setWrite(scope?.save_on_request ?? true);
    setOrganize(scope?.auto_organize ?? false);
    setTimezone(scope?.timezone ?? account.memory?.timezone ?? "UTC");
    setExpectedVersion(scope?.version);
  }
  const save = useMutation({
    mutationFn: async () => {
      if (!group) throw new Error(t("Choose a configured conversation."));
      await client.http
        .POST("/api/v1/application-accounts/{account_id}/memory-scopes", {
          params: { path: { account_id: account.id } },
          body: {
            external_conversation_id: group,
            expected_version: expectedVersion,
            enabled,
            visibility,
            use_memory: read,
            save_on_request: write,
            auto_organize: organize,
            timezone,
          },
        })
        .then(data);
      await refreshMemory(cache, account.id);
      close();
    },
  });
  return (
    <form
      className={shared.stack}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ErrorNotice error={choices.error} />
      {choices.isPending ? (
        <Loading />
      ) : (
        <ChoiceField
          label={t("Conversation")}
          value={group}
          required
          readOnly={!!target || !!initialScope}
          options={(choices.data?.targets ?? [])
            .filter((target) => target.target_kind === "conversation")
            .map((target) => ({
              value: target.external_target_id,
              label:
                choices.data?.scopes.find(
                  (item) =>
                    item.external_conversation_id === target.external_target_id,
                )?.name ?? target.external_target_id,
            }))}
          onValueChange={(value) => {
            setGroup(value);
            const selected = choices.data?.scopes.find(
              (item) => item.external_conversation_id === value,
            );
            setExpectedVersion(selected?.version);
            setOrganize(selected?.auto_organize ?? false);
            setEnabled(selected?.enabled ?? true);
            setVisibility(selected?.visibility ?? "group");
            setRead(selected?.use_memory ?? true);
            setWrite(selected?.save_on_request ?? true);
            setTimezone(
              selected?.timezone ?? account.memory?.timezone ?? "UTC",
            );
          }}
        />
      )}
      <Label>
        <Switch checked={enabled} onCheckedChange={setEnabled} />
        {t("Enable group memory")}
      </Label>
      <div className={styles.groupMemoryCapabilities} data-disabled={!enabled}>
        <Label>
          <Switch
            checked={read}
            onCheckedChange={(value) => {
              setRead(value);
              if (!value) setOrganize(false);
            }}
            disabled={!enabled}
          />
          {t("Refer to memory when answering")}
        </Label>
        <Label>
          <Switch
            checked={write}
            onCheckedChange={(value) => {
              setWrite(value);
              if (!value) setOrganize(false);
            }}
            disabled={!enabled}
          />
          {t("Allow saving or deleting memory through chat")}
        </Label>
      </div>
      {account.memory?.auto_organize && (
        <Label>
          <Switch
            checked={organize}
            onCheckedChange={setOrganize}
            disabled={!enabled || !read || !write}
          />
          {t("Automatic organization")}
        </Label>
      )}
      <ChoiceField
        label={t("Who can read this group's memory?")}
        value={visibility}
        onValueChange={(value) =>
          setVisibility(value as "group" | "installation")
        }
        options={[
          { value: "group", label: t("Only this group") },
          {
            value: "installation",
            label: t(
              account.provider_key === "slack"
                ? "All connected channels in this Slack workspace"
                : "All connected groups in this Feishu enterprise",
            ),
            disabled:
              !scope ||
              scope.backend_type === "a13n.filesystem" ||
              !["public", "private"].includes(scope.audience),
          },
        ]}
      />
      {visibility === "installation" && (
        <p>
          {t(
            "Limited to groups connected to this bot. Other groups can use this group's memory when answering.",
          )}
        </p>
      )}
      <p>
        {t(
          visibility === "installation"
            ? "All existing and future memory in this group can be read by other groups connected to this bot in the same Slack workspace or Feishu tenant. Their private memory stays private."
            : "Other groups cannot read this group's memory. This group can still read memory that other groups make visible.",
        )}
      </p>
      {visibility !== (scope?.visibility ?? "group") && (
        <p role="status">
          {t(
            visibility === "group"
              ? "Saving immediately stops future reads from other groups. Messages already sent to chats are not removed."
              : "This includes historical memory and groups connected to this bot later. No chat message or memory copy is created.",
          )}
        </p>
      )}
      <p>
        {t(
          "Only verified group conversations can open their memory to other groups. Direct conversations stay private.",
        )}
      </p>
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        disabled={choices.isPending || !!choices.error || !group}
        onCancel={close}
      />
    </form>
  );
}
