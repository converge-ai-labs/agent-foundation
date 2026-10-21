import { PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState, type ComponentProps, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ApiError } from "../../../service-client";
import { allPages, data, ifMatch, rowTag } from "../../../shared/api";
import { ConflictNotice } from "../../../shared/dialogs";
import { ErrorNotice } from "../../../shared/feedback";
import { FormActions, SecretReveal } from "../../../shared/forms";
import shared from "../../../shared/shared.module.css";
import { webhookKeys, type Subscription } from "./api";
import {
  rebaseDraft,
  scheduleOutcomeKinds,
  subscriptionBody,
  subscriptionDraft,
  subscriptionPatch,
} from "./draft";
import { SubscriptionFields } from "./fields";
import styles from "./webhooks.module.css";

export function WebhookEditor({
  subscription,
  scheduleId,
  open: controlledOpen,
  onOpenChange,
  trigger,
  finalFocus,
}: {
  subscription?: Subscription;
  scheduleId?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: ReactElement | false;
  finalFocus?: ComponentProps<typeof ModalFrame>["finalFocus"];
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace(),
    navigate = useNavigate();
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const setOpen = (value: boolean) => {
    setLocalOpen(value);
    onOpenChange?.(value);
  };
  const [pending, setPending] = useState(false);
  const [secret, setSecret] = useState("");
  const [created, setCreated] = useState<Subscription>();
  function close() {
    setOpen(false);
    setSecret("");
    setCreated(undefined);
  }
  return (
    <ModalFrame
      size="lg"
      finalFocus={finalFocus}
      open={open}
      onOpenChange={(value, details) => {
        if (pending) {
          details.cancel();
          return;
        }
        if (value) setOpen(true);
        else close();
      }}
      title={t(
        created
          ? "Webhook created"
          : subscription
            ? "Edit Webhook"
            : "Add Webhook",
      )}
      description={t(
        created
          ? "Save the signing secret in your receiving application."
          : "Only matching lifecycle changes generate notifications. Changes affect newly queued deliveries.",
      )}
      closeLabel={t("Close")}
      trigger={
        trigger === false
          ? undefined
          : (trigger ?? (
              <Button
                type="button"
                variant={subscription ? "outline" : "default"}
              >
                {!subscription && <PlusIcon size={14} />}
                {t(subscription ? "Edit" : "Add Webhook")}
              </Button>
            ))
      }
    >
      {open &&
        (created ? (
          <div className={shared.stack}>
            <SecretReveal value={secret} label={t("Signing secret")} wrap />
            <p className={styles.note}>
              {t(
                "Verify X-A13n-Webhook-Signature with this secret and deduplicate by X-A13n-Delivery-Id.",
              )}
            </p>
            <div className={styles.toolbar}>
              <span />
              <Button
                type="button"
                onClick={() => {
                  const id = created.id;
                  close();
                  void navigate(`${basePath}/settings/webhooks/${id}`);
                }}
              >
                {t("I saved the secret")}
              </Button>
            </div>
          </div>
        ) : (
          <WebhookForm
            subscription={subscription}
            scheduleId={scheduleId}
            onPendingChange={setPending}
            onSecret={setSecret}
            onCancel={close}
            onSaved={(saved) => {
              if (subscription) close();
              else setCreated(saved);
            }}
            onExisting={(existing) => {
              close();
              void navigate(
                `${basePath}/settings/webhooks/${existing.id}?tab=configuration`,
              );
            }}
          />
        ))}
    </ModalFrame>
  );
}

function WebhookForm({
  subscription,
  scheduleId,
  onSaved,
  onExisting,
  onSecret,
  onPendingChange,
  onCancel,
}: {
  subscription?: Subscription;
  scheduleId?: string;
  onSaved: (value: Subscription) => void;
  onExisting: (value: Subscription) => void;
  onSecret: (secret: string) => void;
  onPendingChange: (pending: boolean) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const cache = useQueryClient(),
    http = client.workspace(workspace.id),
    keys = webhookKeys(workspace.id);
  const [basis, setBasis] = useState(subscription);
  const [draft, setDraft] = useState(() => ({
    ...subscriptionDraft(subscription),
    ...(!subscription && scheduleId
      ? { scheduleId, kinds: [...scheduleOutcomeKinds] }
      : {}),
  }));
  const [kindsError, setKindsError] = useState<string>();
  const [unconfirmed, setUnconfirmed] = useState(false);
  const reconcile = useMutation({
    mutationFn: () =>
      allPages((cursor) =>
        http
          .GET("/api/v1/subscriptions", {
            params: { query: { cursor, limit: 100 } },
          })
          .then(data),
      ),
  });
  const reload = useMutation({
    mutationFn: () =>
      http
        .GET("/api/v1/subscriptions/{subscription_id}", {
          params: { path: { subscription_id: basis!.id } },
        })
        .then(data),
    onSuccess: (latest) => {
      setDraft((current) => rebaseDraft(current, basis!, latest));
      setBasis(latest);
      save.reset();
      cache.setQueryData(keys.subscription(latest.id), latest);
    },
  });
  const save = useMutation({
    gcTime: 0,
    retry: false,
    onMutate: () => onPendingChange(true),
    mutationFn: async () => {
      if (basis) {
        return data(
          await http.PATCH("/api/v1/subscriptions/{subscription_id}", {
            params: { path: { subscription_id: basis.id } },
            headers: ifMatch(rowTag(basis)),
            body: subscriptionPatch(draft, basis),
          }),
        );
      }
      const { signing_secret, ...saved } = data(
        await http.POST("/api/v1/subscriptions", {
          body: subscriptionBody(draft),
        }),
      );
      // The one-time secret stays in the open dialog, never in either QueryClient cache.
      onSecret(signing_secret);
      return saved;
    },
    onSuccess: (saved) => {
      setDraft((current) => ({ ...current, signingSecret: "" }));
      cache.setQueryData(keys.subscription(saved.id), saved);
      void cache.invalidateQueries({ queryKey: keys.list });
      onSaved(saved);
    },
    onError: (error) => {
      if (!basis && (!(error instanceof ApiError) || error.status >= 500)) {
        setUnconfirmed(true);
        reconcile.mutate();
      }
    },
    onSettled: () => onPendingChange(false),
  });
  const stale = save.error instanceof ApiError && save.error.status === 412;
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (save.isPending || reload.isPending || stale || unconfirmed) return;
        if (!draft.kinds.length) {
          setKindsError(t("Select at least one event."));
          return;
        }
        if (!draft.name.trim()) return;
        save.mutate();
      }}
    >
      <fieldset
        className={styles.fields}
        disabled={save.isPending || reload.isPending || unconfirmed}
      >
        <SubscriptionFields
          draft={draft}
          editing={!!basis}
          kindsError={kindsError}
          onChange={(value) => {
            setDraft(value);
            setKindsError(undefined);
          }}
        />
      </fieldset>
      {stale ? (
        <ConflictNotice
          title={t("This Webhook changed")}
          description={t(
            "Your edits are preserved. Load the current version before saving again.",
          )}
          recover={{
            label: t("Load current version and keep my edits"),
            onClick: () => reload.mutate(),
            pending: reload.isPending,
          }}
        />
      ) : (
        !unconfirmed && <ErrorNotice error={save.error} />
      )}
      <ErrorNotice error={reload.error} />
      {unconfirmed && (
        <ConflictNotice
          title={t("Creation could not be confirmed")}
          description={t(
            "Review existing Webhooks before trying again. If one was created, its secret cannot be recovered; edit it to set a replacement.",
          )}
          recover={{
            label: t("Review existing Webhooks"),
            onClick: () => reconcile.mutate(),
            pending: reconcile.isPending,
          }}
          proceed={
            reconcile.isSuccess
              ? {
                  label: t("I checked; allow another create attempt"),
                  onClick: () => {
                    setUnconfirmed(false);
                    reconcile.reset();
                    save.reset();
                  },
                }
              : undefined
          }
        >
          <ErrorNotice error={reconcile.error} />
          {reconcile.data && (
            <div className={shared.stack}>
              {reconcile.data.length ? (
                reconcile.data.map((item) => (
                  <div key={item.id} className={styles.toolbar}>
                    <span className={styles.stacked}>
                      <span>{item.name}</span>
                      <span className={styles.note}>{item.url}</span>
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onExisting(item)}
                    >
                      {t("Open Webhook")}
                    </Button>
                  </div>
                ))
              ) : (
                <p>{t("No saved Webhooks found in this workspace.")}</p>
              )}
            </div>
          )}
        </ConflictNotice>
      )}
      <FormActions
        pending={save.isPending}
        disabled={
          stale || unconfirmed || reload.isPending || !draft.name.trim()
        }
        onCancel={onCancel}
        label={t(basis ? "Save changes" : "Create Webhook")}
      />
    </form>
  );
}
