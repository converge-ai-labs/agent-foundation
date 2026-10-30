import { PlusIcon } from "@phosphor-icons/react";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Section } from "../../../shared/page";
import { MemoryMountRows, MountForm } from "../../memories/mounts";
import { SessionMemoryFields } from "../../memories/session-settings";
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
  // Space participation and individual memories share one editor group.
  return (
    <>
      <SessionMemoryFields
        compact
        value={draft.memory}
        onChange={draft.setMemory}
        disabled={readOnly}
      />
      <Section
        title={t("Memories")}
        description={t("Available to new sessions.")}
        actions={
          !readOnly && (
            <ModalFrame
              open={open}
              onOpenChange={setOpen}
              title={t("Add memory")}
              description={t("Choose a memory for new sessions.")}
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
          empty={readOnly ? t("No default memories.") : undefined}
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
    </>
  );
}
