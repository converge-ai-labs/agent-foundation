import { PlusIcon } from "@phosphor-icons/react";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Section } from "../../../shared/page";
import { MemoryMountRows, MountForm } from "../../memories/mounts";
import type { AgentDraft } from "./draft";

/**
 * Memories a conversation with this agent mounts at its first run. The thread
 * keeps them afterwards, so later changes here reach new conversations only.
 */
export function MemorySection({
  draft,
  readOnly,
}: {
  draft: AgentDraft;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const mounts = draft.memoryMounts;
  return (
    <Section
      title={t("Memory")}
      description={t(
        "What the agent keeps across conversations. New conversations mount these memories.",
      )}
      actions={
        !readOnly && (
          <ModalFrame
            open={open}
            onOpenChange={setOpen}
            title={t("Add memory")}
            description={t(
              "New conversations with this agent mount the memory from their first run.",
            )}
            closeLabel={t("Close")}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon size={14} />
                {t("Add memory")}
              </Button>
            }
          >
            {open && (
              <MountForm
                mounts={mounts}
                label={t("Add memory")}
                onSubmit={(mount) => {
                  draft.setMemoryMounts((previous) => [...previous, mount]);
                  setOpen(false);
                }}
                onCancel={() => setOpen(false)}
              />
            )}
          </ModalFrame>
        )
      }
    >
      <MemoryMountRows
        mounts={mounts}
        empty={t("No default memories.")}
        onAccessChange={
          readOnly
            ? undefined
            : (name, access) =>
                draft.setMemoryMounts((previous) =>
                  previous.map((mount) =>
                    mount.name === name ? { ...mount, access } : mount,
                  ),
                )
        }
        onRecallChange={
          readOnly
            ? undefined
            : (name, recall) =>
                draft.setMemoryMounts((previous) =>
                  previous.map((mount) =>
                    mount.name === name ? { ...mount, recall } : mount,
                  ),
                )
        }
        onRemove={
          readOnly
            ? undefined
            : (name) =>
                draft.setMemoryMounts((previous) =>
                  previous.filter((mount) => mount.name !== name),
                )
        }
      />
    </Section>
  );
}
