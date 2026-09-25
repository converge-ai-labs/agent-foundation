import {
  BrainIcon,
  ClockCounterClockwiseIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ListRow, ListRows, ListRowsEmpty } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, useSuggestedName } from "../../shared/forms";
import { memoryQueries } from "./api";

type Mount = Schema["MemoryMount"];
type Access = Schema["MemoryAccess"];

/** The model addresses a mounted memory by this name. */
export const MOUNT_NAME_PATTERN = "[a-z][a-z0-9-]{0,62}";

/** A mount name suggested from a memory key, such as `team-handbook`. */
export function mountName(key: string): string {
  return (
    key
      .toLowerCase()
      .replace(/[^a-z0-9-]+/g, "-")
      .replace(/^[^a-z]+/, "")
      .slice(0, 63)
      .replace(/-+$/, "") || "memory"
  );
}

/** Every memory of the workspace, which pickers offer and rows name. */
export function useMemoryChoices() {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery(memoryQueries(client, workspace.id).choices());
}

function accessOptions(t: (key: string) => string) {
  return [
    { value: "read", label: t("Read") },
    { value: "write", label: t("Write") },
  ];
}

/**
 * Chooses a memory, the name the model will address it by, and whether the
 * model may change it. Memories and names already mounted are refused here
 * rather than by the Service.
 */
export function MountForm({
  mounts,
  label,
  pending = false,
  error,
  onSubmit,
  onCancel,
}: {
  mounts: readonly Mount[];
  label: string;
  pending?: boolean;
  error?: unknown;
  onSubmit: (mount: Mount) => void;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const choices = useMemoryChoices();
  const [memoryId, setMemoryId] = useState("");
  const [access, setAccess] = useState<Access>("write");
  const { name, setName, suggestName } = useSuggestedName();
  const taken = mounts.some((mount) => mount.name === name);
  const available = (choices.data ?? []).filter(
    (memory) => !mounts.some((mount) => mount.memory_id === memory.id),
  );
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        // The form may sit in a dialog whose owner is itself a form.
        event.preventDefault();
        event.stopPropagation();
        if (memoryId && !taken) onSubmit({ name, memory_id: memoryId, access });
      }}
    >
      <ErrorNotice error={choices.error} retry={() => void choices.refetch()} />
      <ChoiceField
        label={t("Memory")}
        placeholder={choices.isPending ? t("Loading…") : t("Select memory")}
        required
        value={memoryId}
        onValueChange={(value) => {
          setMemoryId(value);
          const memory = available.find((item) => item.id === value);
          if (memory) suggestName(mountName(memory.key));
        }}
        options={available.map((memory) => ({
          value: memory.id,
          label: `${memory.name} (${memory.key})`,
        }))}
      />
      <FormField
        label={t("Mount name")}
        description={t(
          "The model addresses the memory by this name, such as prefs.",
        )}
        error={
          taken ? t("A memory is already mounted under this name.") : undefined
        }
      >
        <Input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          pattern={MOUNT_NAME_PATTERN}
          maxLength={63}
          autoComplete="off"
        />
      </FormField>
      <ChoiceField
        label={t("Access")}
        description={t(
          "Read offers viewing and searching; write offers every memory tool.",
        )}
        value={access}
        onValueChange={(value) =>
          setAccess(value === "read" ? "read" : "write")
        }
        options={accessOptions(t)}
      />
      <ErrorNotice error={error} />
      <FormActions
        pending={pending}
        onCancel={onCancel}
        label={label}
        disabled={!memoryId || taken}
      />
    </form>
  );
}

/**
 * Mounted memories by name, each naming its memory with a way in. Draft lists
 * change access in place; a thread's list removes and adds instead.
 */
export function MemoryMountRows({
  mounts,
  empty,
  runId,
  onAccessChange,
  onRemove,
}: {
  mounts: readonly Mount[];
  empty: ReactNode;
  /** Links each memory's history to the changes of this run. */
  runId?: string;
  onAccessChange?: (name: string, access: Access) => void;
  onRemove?: (name: string) => void;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const choices = useMemoryChoices();
  if (!mounts.length) return <ListRowsEmpty>{empty}</ListRowsEmpty>;
  return (
    <ListRows>
      {mounts.map((mount) => {
        const memory = choices.data?.find(
          (item) => item.id === mount.memory_id,
        );
        const path = `${basePath}/memories/${encodeURIComponent(mount.memory_id)}`;
        return (
          <ListRow
            key={mount.name}
            icon={<BrainIcon size={16} />}
            name={mount.name}
            secondary={
              memory ? (
                <Link to={path}>{memory.name}</Link>
              ) : choices.isPending ? (
                mount.memory_id
              ) : (
                `${t("Memory unavailable")} · ${mount.memory_id}`
              )
            }
            control={
              onAccessChange ? (
                <ChoiceField
                  label={t("Access for {{name}}", { name: mount.name })}
                  hideLabel
                  className="w-28 [&_[data-slot=select-trigger]]:min-w-0"
                  value={mount.access}
                  onValueChange={(value) =>
                    onAccessChange(
                      mount.name,
                      value === "read" ? "read" : "write",
                    )
                  }
                  options={accessOptions(t)}
                />
              ) : (
                t(mount.access === "read" ? "Read" : "Write")
              )
            }
            actions={
              <>
                {runId && (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    render={
                      <Link
                        to={`${path}?tab=history&run=${encodeURIComponent(runId)}`}
                      />
                    }
                    aria-label={t("Changes by this run to {{name}}", {
                      name: mount.name,
                    })}
                    title={t("Changes by this run")}
                  >
                    <ClockCounterClockwiseIcon />
                  </Button>
                )}
                {onRemove && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Remove {{name}}", { name: mount.name })}
                    onClick={() => onRemove(mount.name)}
                  >
                    <XIcon />
                  </Button>
                )}
              </>
            }
          />
        );
      })}
    </ListRows>
  );
}
