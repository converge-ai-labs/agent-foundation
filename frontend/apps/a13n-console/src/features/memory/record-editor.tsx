import { ApiError } from "@converge.ai/a13n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  FormField,
  ModalFrame,
  Textarea,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { CopyableId } from "../../shared/copy";
import { memoryApi, memoryKey, type MemoryTarget } from "./api";

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
  const [pending, setPending] = useState(false);
  const record = useQuery({
    queryKey: [...memoryKey(target), "record", recordId],
    enabled: !!recordId,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
    queryFn: ({ signal }) => api.get(recordId!, signal),
  });
  return (
    <ModalFrame
      open
      onOpenChange={(open, details) => {
        if (pending) details.cancel();
        else if (!open) onClose();
      }}
      size="lg"
      title={t(recordId ? "Memory" : "Add memory")}
      closeLabel={t("Close")}
    >
      <ErrorNotice error={accessError} retry={retryAccess} />
      {recordId && record.isPending ? (
        <Loading variant="form" rows={3} />
      ) : record.error ? (
        <ErrorNotice error={record.error} retry={() => void record.refetch()} />
      ) : (
        <MemoryRecordForm
          target={target}
          recordId={recordId}
          initial={record.data?.memory ?? ""}
          canWrite={canWrite}
          onPending={setPending}
          onClose={onClose}
        />
      )}
    </ModalFrame>
  );
}

function MemoryRecordForm({
  target,
  recordId,
  initial,
  canWrite,
  onPending,
  onClose,
}: {
  target: MemoryTarget;
  recordId?: string;
  initial: string;
  canWrite: boolean;
  onPending: (value: boolean) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const api = memoryApi(client, target);
  const [text, setText] = useState(initial);
  const [deleting, setDeleting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [observed, setObserved] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<unknown>();
  const mutation = useMutation({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      onPending(true);
      if (deleting) return api.remove(recordId!);
      if (recordId) return api.update(recordId, text);
      return api.add(text);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: memoryKey(target) });
      onClose();
    },
    onError: (error) => {
      if (
        !(error instanceof ApiError) ||
        error.status >= 500 ||
        error.code === "memory_write_unconfirmed"
      ) {
        setUncertain(true);
        setObserved(null);
      }
    },
    onSettled: () => onPending(false),
  });
  async function inspect() {
    setChecking(true);
    onPending(true);
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
      onPending(false);
    }
  }
  const locked = mutation.isPending || checking;
  return (
    <form
      className="flex min-w-0 flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!locked && !uncertain && canWrite) mutation.mutate();
      }}
    >
      {recordId && <CopyableId value={recordId} />}
      {canWrite ? (
        <FormField
          label={t("Memory text")}
          description={t(
            "Saved exactly as written, without automatic extraction. Maximum 8,000 characters.",
          )}
        >
          <Textarea
            rows={9}
            required
            maxLength={8000}
            value={text}
            disabled={locked || deleting || uncertain}
            onChange={(event) => setText(event.target.value)}
          />
        </FormField>
      ) : (
        <div className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words text-sm">
          {text}
        </div>
      )}
      {deleting && (
        <Alert variant="warning">
          <AlertTitle>{t("Delete this memory?")}</AlertTitle>
          <AlertDescription>
            {t(
              "This removes the record from its backend. This action cannot be undone.",
            )}
          </AlertDescription>
        </Alert>
      )}
      <ErrorNotice error={mutation.error} />
      {uncertain && (
        <Alert variant="warning">
          <AlertTitle>{t("Change not confirmed")}</AlertTitle>
          <AlertDescription>
            <p>
              {t(
                "The backend may have applied this change. Your draft is preserved. Inspect the current state before making another explicit attempt.",
              )}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={locked}
                onClick={() => void inspect()}
              >
                {t(checking ? "Checking…" : "Inspect current state")}
              </Button>
              {observed !== null && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={locked}
                  onClick={() => {
                    setUncertain(false);
                    mutation.reset();
                  }}
                >
                  {t("I checked; allow another attempt")}
                </Button>
              )}
            </div>
            {observed !== null && (
              <div className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words text-sm">
                {observed}
              </div>
            )}
          </AlertDescription>
        </Alert>
      )}
      <ErrorNotice error={checkError} />
      <footer
        data-a13n-form-actions
        className="flex flex-wrap items-center justify-end gap-2 border-t pt-4"
      >
        {canWrite && recordId && (
          <Button
            type="button"
            variant="ghost"
            className="mr-auto"
            disabled={locked || uncertain}
            onClick={() => setDeleting(!deleting)}
          >
            {t(deleting ? "Keep memory" : "Delete memory")}
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={locked}
          onClick={onClose}
        >
          {t(canWrite ? "Cancel" : "Close")}
        </Button>
        {canWrite && (
          <Button
            type="submit"
            variant={deleting ? "destructive" : "default"}
            disabled={
              locked ||
              uncertain ||
              (!deleting &&
                (!text.trim() ||
                  text.length > 8000 ||
                  (!!recordId && text === initial)))
            }
          >
            {t(
              mutation.isPending
                ? "Saving…"
                : deleting
                  ? "Confirm deletion"
                  : "Save",
            )}
          </Button>
        )}
      </footer>
    </form>
  );
}
