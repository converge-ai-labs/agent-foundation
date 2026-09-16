import type { BotAccount } from "./account";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  Label,
  ModalFrame,
  Switch,
} from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import shared from "../../shared/shared.module.css";
import { BotChecks } from "./checks";

export function MemorySettings({
  account,
  reload,
}: {
  account: BotAccount;
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation();
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Bot memory settings")}
      closeLabel={t("Close")}
      size="md"
      trigger={
        <Button variant="outline" onClick={() => setOpen(true)}>
          {t("Memory settings")}
        </Button>
      }
    >
      {open && (
        <SettingsForm
          account={account}
          close={() => setOpen(false)}
          reload={reload}
        />
      )}
    </ModalFrame>
  );
}

function SettingsForm({
  account,
  close,
  reload,
}: {
  account: BotAccount;
  close: () => void;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [providerId, setProviderId] = useState(
    account.memory?.provider_id ?? "none",
  );
  const [useMemory, setUseMemory] = useState(
    account.memory?.use_memory ?? true,
  );
  const [saveMemory, setSaveMemory] = useState(
    account.memory?.save_on_request ?? true,
  );
  const [timezone, setTimezone] = useState(
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
    providerId !== "none" &&
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
            memory:
              providerId === "none"
                ? null
                : {
                    provider_id: providerId,
                    use_memory: useMemory,
                    save_on_request: saveMemory,
                    timezone,
                  },
          },
        })
        .then(data);
      await reload();
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
      <p>
        {t(
          "Only workspace administrators can view and manage connected private-group memory.",
        )}
      </p>
      <ErrorNotice error={providers.error ?? types.error} />
      {providers.isPending || types.isPending ? (
        <Loading />
      ) : (
        <ChoiceField
          label={t("Memory Provider")}
          value={providerId}
          onValueChange={setProviderId}
          options={[
            { value: "none", label: t("Disabled") },
            ...(providers.data ?? []).map((item) => ({
              value: item.id,
              label: `${item.name}${!item.enabled ? ` · ${t("Disabled")}` : support.get(item.type) === false ? ` · ${t("Document memory unsupported")}` : support.get(item.type) !== true ? ` · ${t("Unavailable")}` : ""}`,
              disabled: !item.enabled || support.get(item.type) !== true,
            })),
          ]}
        />
      )}
      {unavailable && !providers.isPending && !types.isPending && (
        <p role="note">
          {t("Choose a Provider that supports document memory.")}
        </p>
      )}
      {account.memory && providerId !== account.memory.provider_id && (
        <p role="note">
          {t(
            "Existing documents stay on the previous Provider. This does not migrate or delete them.",
          )}
        </p>
      )}
      {providerId !== "none" && (
        <>
          <Label>
            <Switch checked={useMemory} onCheckedChange={setUseMemory} />
            {t("Use memory during conversations")}
          </Label>
          <Label>
            <Switch checked={saveMemory} onCheckedChange={setSaveMemory} />
            {t("Allow explicit save and forget requests")}
          </Label>
          <FormField label={t("Time zone")}>
            <Input
              value={timezone}
              onChange={(event) => setTimezone(event.target.value)}
              required
            />
          </FormField>
          <p>
            {t(
              "Groups inherit this Provider. Configure each group separately; sharing is off by default.",
            )}
          </p>
        </>
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        disabled={unavailable}
        onCancel={close}
      />
    </form>
  );
}

export function GroupMemorySettings({
  account,
  target,
}: {
  account: BotAccount;
  target?: Schema["AccountTarget"];
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Configure group memory")}
      closeLabel={t("Close")}
      size="md"
      trigger={
        <Button variant="outline" onClick={() => setOpen(true)}>
          {t("Configure group")}
        </Button>
      }
    >
      {open && (
        <GroupForm
          account={account}
          target={target}
          close={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}

function GroupForm({
  account,
  close,
  target,
}: {
  account: BotAccount;
  target?: Schema["AccountTarget"];
  close: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [group, setGroup] = useState(target?.external_target_id ?? ""),
    [enabled, setEnabled] = useState(true),
    [read, setRead] = useState(true),
    [write, setWrite] = useState(true);
  const [timezone, setTimezone] = useState(account.memory?.timezone ?? "UTC");
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
  if (target && choices.data && !loaded) {
    setLoaded(true);
    setEnabled(scope?.enabled ?? true);
    setRead(scope?.use_memory ?? true);
    setWrite(scope?.save_on_request ?? true);
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
            use_memory: read,
            save_on_request: write,
            timezone,
          },
        })
        .then(data);
      await cache.invalidateQueries({
        queryKey: ["bot-memory-scopes", account.id],
      });
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
          readOnly={!!target}
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
            setEnabled(selected?.enabled ?? true);
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
      <Label>
        <Switch checked={read} onCheckedChange={setRead} />
        {t("Use memory during conversations")}
      </Label>
      <Label>
        <Switch checked={write} onCheckedChange={setWrite} />
        {t("Allow explicit save and forget requests")}
      </Label>
      <FormField label={t("Time zone")}>
        <Input
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
          required
        />
      </FormField>
      <p>
        {t(
          "The platform must verify this conversation before the bot can use its memory. These settings do not grant sharing access.",
        )}
      </p>
      {group && (
        <BotChecks key={group} account={account} conversationId={group} />
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        disabled={choices.isPending || !!choices.error || !group}
        onCancel={close}
      />
    </form>
  );
}
