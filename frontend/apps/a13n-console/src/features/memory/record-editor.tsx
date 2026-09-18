import { DotsThreeOutlineVerticalIcon, TrashIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../service-client";
import { useClient } from "../../auth/context";
import { Confirm, ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { CopyableId } from "../../shared/identity";
import { Panel } from "../../shared/page";
import { memoryApi, memoryKey, type MemoryTarget } from "./api";
import styles from "./memory.module.css";

const maxLength = 8000;

/** A write whose outcome the backend never confirmed must not be repeated. */
function unconfirmed(error: unknown) {
  return (
    !(error instanceof ApiError) ||
    error.status >= 500 ||
    error.code === "memory_write_unconfirmed"
  );
}

export function MemoryRecordEditor({
  target,
  recordId,
  canWrite,
  onClose,
  accessError,
  retryAccess,
}: {
  target: MemoryTarget;
  recordId?: string;
  canWrite: boolean;
  onClose: () => void;
  accessError?: unknown;
  retryAccess?: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const api = memoryApi(client, target);
  const record = useQuery({
    queryKey: [...memoryKey(target), "record", recordId],
    enabled: !!recordId,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
    queryFn: ({ signal }) => api.get(recordId!, signal),
  });
  const title = recordId ? t("Memory") : t("Add memory");
  if (recordId && record.isPending)
    return (
      <Panel open title={title} label={title} onClose={onClose}>
        <ErrorNotice error={accessError} retry={retryAccess} />
        <Loading variant="form" rows={3} />
      </Panel>
    );
  if (record.error)
    return (
      <Panel open title={title} label={title} onClose={onClose}>
        <ErrorNotice error={accessError} retry={retryAccess} />
        <ErrorNotice error={record.error} retry={() => void record.refetch()} />
      </Panel>
    );
  return (
    <MemoryRecordForm
      target={target}
      recordId={recordId}
      title={title}
      initial={record.data?.memory ?? ""}
      canWrite={canWrite}
      accessError={accessError}
      retryAccess={retryAccess}
      onClose={onClose}
    />
  );
}

function MemoryRecordForm({
  target,
  recordId,
  title,
  initial,
  canWrite,
  accessError,
  retryAccess,
  onClose,
}: {
  target: MemoryTarget;
  recordId?: string;
  title: string;
  initial: string;
  canWrite: boolean;
  accessError?: unknown;
  retryAccess?: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const api = memoryApi(client, target);
  const [text, setText] = useState(initial);
  const [uncertain, setUncertain] = useState(false);
  const [observed, setObserved] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<unknown>();
  const [removeError, setRemoveError] = useState<unknown>();
  const [removing, setRemoving] = useState(false);
  const removed = useRef(false);
  const mutation = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: () => (recordId ? api.update(recordId, text) : api.add(text)),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: memoryKey(target) });
      onClose();
    },
    onError: (error) => {
      if (unconfirmed(error)) {
        setUncertain(true);
        setObserved(null);
      }
    },
  });
  /** One DELETE, whatever happens: an unconfirmed removal recovers here. */
  async function remove() {
    removed.current = false;
    setRemoveError(undefined);
    setRemoving(true);
    try {
      await api.remove(recordId!);
      removed.current = true;
    } catch (error) {
      setRemoveError(error);
      if (unconfirmed(error)) {
        setUncertain(true);
        setObserved(null);
      }
    } finally {
      setRemoving(false);
    }
  }
  function afterRemove() {
    if (!removed.current) return;
    void cache.invalidateQueries({ queryKey: memoryKey(target) });
    onClose();
  }
  async function inspect() {
    setChecking(true);
    setCheckError(undefined);
    try {
      if (recordId) {
        try {
          setObserved((await api.get(recordId)).memory);
        } catch (error) {
          if (error instanceof ApiError && error.code === "memory_not_found")
            setObserved(t("This memory is no longer present."));
          else throw error;
        }
      } else {
        const matches = await api.search(
          { query: text, limit: 100, threshold: null },
          new AbortController().signal,
        );
        setObserved(
          [
            t(
              "Current semantic matches are shown below. A missing match does not prove the write failed; another attempt may create a duplicate.",
            ),
            ...matches.items.map((item) => `${item.id}\n${item.memory}`),
          ].join("\n\n"),
        );
      }
    } catch (error) {
      setCheckError(error);
    } finally {
      setChecking(false);
    }
  }
  const locked = mutation.isPending || removing || checking;
  return (
    <Panel
      open
      title={title}
      label={title}
      onClose={() => {
        if (!locked) onClose();
      }}
      actions={
        canWrite && recordId ? (
          <Menu>
            <MenuTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("Memory actions")}
                  title={t("Memory actions")}
                />
              }
            >
              <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
            </MenuTrigger>
            <MenuPopup align="end">
              <Confirm
                subject={initial.slice(0, 120) || recordId}
                title={t("Delete memory")}
                description={t(
                  "This removes the record from its backend. This action cannot be undone.",
                )}
                danger
                triggerElement={
                  <MenuItem
                    closeOnClick={false}
                    disabled={locked || uncertain}
                    variant="destructive"
                  >
                    <TrashIcon size={14} />
                    {t("Delete memory")}
                  </MenuItem>
                }
                action={remove}
                onSuccess={afterRemove}
              />
            </MenuPopup>
          </Menu>
        ) : undefined
      }
    >
      <form
        className={styles.recordForm}
        onSubmit={(event) => {
          event.preventDefault();
          if (!locked && !uncertain && canWrite) mutation.mutate();
        }}
      >
        {recordId && <CopyableId value={recordId} />}
        {canWrite ? (
          <TextAreaField
            label={t("Memory text")}
            hint={t(
              "Saved exactly as written, without automatic extraction. Maximum 8,000 characters.",
            )}
            rows={12}
            required
            value={text}
            onChange={(value) => setText(value.slice(0, maxLength))}
          />
        ) : (
          <p className={styles.recordText}>{text}</p>
        )}
        <ErrorNotice error={accessError} retry={retryAccess} />
        <ErrorNotice error={mutation.error ?? removeError} />
        {uncertain && (
          <ConflictNotice
            title={t("Change not confirmed")}
            description={t(
              "The backend may have applied this change. Your draft is preserved. Inspect the current state before making another explicit attempt.",
            )}
            recover={{
              label: t("Inspect current state"),
              pending: checking,
              onClick: () => void inspect(),
            }}
            proceed={
              observed !== null
                ? {
                    label: t("I checked; allow another attempt"),
                    onClick: () => {
                      setUncertain(false);
                      setRemoveError(undefined);
                      mutation.reset();
                    },
                  }
                : undefined
            }
          >
            {observed !== null && <p className={styles.observed}>{observed}</p>}
          </ConflictNotice>
        )}
        <ErrorNotice error={checkError} />
        {canWrite && (
          <FormActions
            pending={mutation.isPending}
            label={t("Save")}
            onCancel={onClose}
            disabled={
              locked ||
              uncertain ||
              !text.trim() ||
              (!!recordId && text === initial)
            }
          />
        )}
      </form>
    </Panel>
  );
}
