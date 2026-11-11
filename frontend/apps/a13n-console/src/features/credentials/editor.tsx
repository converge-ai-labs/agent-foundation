import { PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame, useToast } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { data, ifMatch, rowTag } from "../../shared/api";
import { ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, FormSection, TextAreaField } from "../../shared/forms";
import shared from "../../shared/shared.module.css";
import { credentialKeys, type Credential } from "./api";
import {
  metadataPatch,
  parseHosts,
  rebaseMetadata,
  validHosts,
  validToken,
} from "./draft";
import styles from "./credentials.module.css";

type Operation =
  { kind: "create" } | { kind: "edit" | "rotate"; credential: Credential };
const titles = {
  create: "Add credential",
  edit: "Edit credential",
  rotate: "Rotate credential",
};

export function useCredentialEditor({
  operation,
  onSaved,
}: {
  operation: Operation;
  onSaved?: (saved: Credential) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const opened = useRef(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const changeOpen = (next: boolean) => {
    opened.current = next;
    setOpen(next);
  };
  const button = (
    <Button
      type="button"
      disabled={pending}
      variant={operation.kind === "create" ? "default" : "outline"}
      aria-haspopup="dialog"
      onClick={(event) => {
        trigger.current = event.currentTarget;
        changeOpen(true);
      }}
    >
      {operation.kind === "create" && <PlusIcon size={14} aria-hidden="true" />}
      {t(titles[operation.kind])}
    </Button>
  );
  const dialog = (
    <ModalFrame
      open={open}
      onOpenChange={changeOpen}
      finalFocus={trigger}
      size="md"
      title={t(titles[operation.kind])}
      closeLabel={t("Close")}
      description={t(
        operation.kind === "rotate"
          ? "Save a new token. Active Sessions update asynchronously; idle Sessions update on next use."
          : "Allow agents to authenticate HTTPS requests to specific domains.",
      )}
    >
      {open && (
        <CredentialForm
          operation={operation}
          onPendingChange={setPending}
          onCancel={() => changeOpen(false)}
          onSaved={(saved) => {
            // A dismissed request may still finish, but it must not navigate or reopen a dialog.
            if (opened.current) {
              changeOpen(false);
              onSaved?.(saved);
            }
          }}
        />
      )}
    </ModalFrame>
  );
  return { button, dialog };
}

function CredentialForm({
  operation,
  onPendingChange,
  onCancel,
  onSaved,
}: {
  operation: Operation;
  onPendingChange: (pending: boolean) => void;
  onCancel: () => void;
  onSaved: (saved: Credential) => void;
}) {
  const toast = useToast();
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const http = client.workspace(workspace.id),
    keys = credentialKeys(workspace.id);
  const [basis, setBasis] = useState(
    operation.kind === "create" ? undefined : operation.credential,
  );
  const [draft, setDraft] = useState({
    name: basis?.name ?? "",
    description: basis?.description ?? "",
  });
  const [hosts, setHosts] = useState("");
  const [token, setToken] = useState("");
  const [validation, setValidation] = useState<{
    hosts?: string;
    token?: string;
  }>({});
  const reload = useMutation({
    mutationFn: (reviewed: Credential) =>
      http
        .GET("/api/v1/egress-credentials/{credential_id}", {
          params: { path: { credential_id: reviewed.id } },
        })
        .then(data),
    onSuccess: (latest, reviewed) => {
      setDraft((current) => rebaseMetadata(current, reviewed, latest));
      setBasis(latest);
      save.reset();
      cache.setQueryData(keys.item(latest.id), latest);
    },
  });
  const save = useMutation({
    gcTime: 0,
    retry: false,
    onMutate: () => onPendingChange(true),
    onSettled: () => onPendingChange(false),
    // Token values stay out of mutation variables, response data and query keys.
    mutationFn: async () => {
      if (!basis)
        return data(
          await http.POST("/api/v1/egress-credentials", {
            body: {
              name: draft.name.trim(),
              description: draft.description,
              allowed_hosts: parseHosts(hosts),
              value: token,
            },
          }),
        );
      const params = { path: { credential_id: basis.id } },
        headers = ifMatch(rowTag(basis));
      return operation.kind === "rotate"
        ? data(
            await http.PUT("/api/v1/egress-credentials/{credential_id}/value", {
              params,
              headers,
              body: { value: token },
            }),
          )
        : data(
            await http.PATCH("/api/v1/egress-credentials/{credential_id}", {
              params,
              headers,
              body: metadataPatch(draft, basis),
            }),
          );
    },
    onSuccess: (saved) => {
      setToken("");
      cache.setQueryData(keys.item(saved.id), saved);
      void cache.invalidateQueries({ queryKey: keys.root });
      toast.add({
        type: "success",
        title: t("Credential version {{version}} saved", {
          version: saved.credential_version,
        }),
      });
      onSaved(saved);
    },
  });
  const unconfirmed =
    !!save.error &&
    (!(save.error instanceof ApiError) || save.error.status >= 500);
  const stale = save.error instanceof ApiError && save.error.status === 412;
  const revoked = operation.kind === "rotate" && basis?.state === "revoked";
  const blocked =
    save.isPending || reload.isPending || stale || unconfirmed || revoked;
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (blocked) return;
        const errors = {
          ...(operation.kind === "create" && !validHosts(parseHosts(hosts))
            ? {
                hosts: t(
                  "Enter 1–256 exact DNS domains, without URLs, IP addresses or wildcards.",
                ),
              }
            : {}),
          ...(operation.kind !== "edit" && !validToken(token)
            ? {
                token: t(
                  "Enter a token of up to 8192 UTF-8 bytes, without line breaks or control characters.",
                ),
              }
            : {}),
        };
        setValidation(errors);
        if (
          Object.keys(errors).length ||
          (operation.kind !== "rotate" && !draft.name.trim())
        )
          return;
        save.mutate();
      }}
    >
      <fieldset
        className={styles.fields}
        disabled={save.isPending || reload.isPending || unconfirmed || revoked}
      >
        {operation.kind !== "rotate" && (
          <FormSection title={t("Basic information")}>
            <FormField label={t("Name")}>
              <Input
                autoFocus
                required
                maxLength={128}
                value={draft.name}
                onChange={(event) =>
                  setDraft({ ...draft, name: event.target.value })
                }
                placeholder={t("For example, GitHub automation")}
              />
            </FormField>
            <TextAreaField
              label={t("Description (optional)")}
              rows={2}
              value={draft.description}
              maxLength={2048}
              onChange={(description) => setDraft({ ...draft, description })}
            />
          </FormSection>
        )}
        {operation.kind === "create" && (
          <FormSection title={t("Allowed domains")}>
            <TextAreaField
              label={t("Domains")}
              rows={3}
              value={hosts}
              onChange={setHosts}
              placeholder="api.github.com"
              error={validation.hosts}
              invalid={!!validation.hosts}
            />
            <p className={styles.note}>
              {t(
                "Separate domains with commas or new lines. Domains cannot be changed after creation.",
              )}
            </p>
          </FormSection>
        )}
        {basis && (
          <div className={styles.summary}>
            <strong>{basis.name}</strong>
            <span>{basis.allowed_hosts.join(", ")}</span>
            <span>
              {t("Credential version {{version}}", {
                version: basis.credential_version,
              })}
            </span>
          </div>
        )}
        {operation.kind !== "edit" && (
          <FormField
            label={t("Token")}
            error={validation.token}
            invalid={!!validation.token}
            description={t(
              "Stored encrypted and never shown again. It is not included in agent context.",
            )}
          >
            <Input
              type="password"
              autoComplete="new-password"
              required
              maxLength={8192}
              autoFocus={operation.kind === "rotate"}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              aria-invalid={!!validation.token}
            />
          </FormField>
        )}
      </fieldset>
      {revoked && (
        <p role="alert">
          {t(
            "This credential was revoked. Create a new credential to resume use.",
          )}
        </p>
      )}
      {stale || unconfirmed ? (
        <ConflictNotice
          title={t(
            stale ? "This credential changed" : "Save could not be confirmed",
          )}
          description={t(
            basis
              ? "Your input is preserved. Load and review the current version before saving again."
              : "Check the credential list before creating another credential.",
          )}
          recover={
            basis
              ? {
                  label: t("Load current version and keep my edits"),
                  onClick: () => reload.mutate(basis),
                  pending: reload.isPending,
                }
              : { label: t("Return to credential list"), onClick: onCancel }
          }
        />
      ) : (
        <ErrorNotice error={save.error} />
      )}
      <ErrorNotice error={reload.error} />
      <FormActions
        onCancel={onCancel}
        pending={save.isPending}
        disabled={blocked}
        label={t(
          operation.kind === "create"
            ? "Create credential"
            : operation.kind === "rotate"
              ? "Save new token"
              : "Save changes",
        )}
      />
    </form>
  );
}
