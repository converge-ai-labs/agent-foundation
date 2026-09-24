import { DisclosureSection } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { MemoryMountRows, MountForm } from "../../memories/mounts";
import type { OptionField, RunOptionsState } from "./options-dialog";

/**
 * Memories the new Thread mounts from its first Run, beside the agent's
 * default mounts. The chosen access and recall can still change before sending.
 */
export function MemoryOptions({
  options,
  focus,
}: {
  options: RunOptionsState;
  focus?: OptionField;
}) {
  const { t } = useTranslation();
  const { memories, setMemories } = options;
  const [expanded, setExpanded] = useState(focus === "memories");
  // Each addition starts the next one from an empty form.
  const [added, setAdded] = useState(0);
  useEffect(() => {
    if (focus === "memories") setExpanded(true);
  }, [focus]);
  return (
    <DisclosureSection
      title={t("Memories")}
      summary={
        memories.length
          ? t("{{count}} memories", { count: memories.length })
          : t("Agent defaults")
      }
      open={expanded}
      onOpenChange={setExpanded}
    >
      <MemoryMountRows
        mounts={memories}
        empty={t("The agent's default memories only.")}
        onAccessChange={(name, access) =>
          setMemories(
            memories.map((mount) =>
              mount.name === name ? { ...mount, access } : mount,
            ),
          )
        }
        onRecallChange={(name, recall) =>
          setMemories(
            memories.map((mount) =>
              mount.name === name ? { ...mount, recall } : mount,
            ),
          )
        }
        onRemove={(name) =>
          setMemories(memories.filter((mount) => mount.name !== name))
        }
      />
      <MountForm
        key={added}
        mounts={memories}
        label={t("Add memory")}
        onSubmit={(mount) => {
          setMemories([...memories, mount]);
          setAdded(added + 1);
        }}
      />
    </DisclosureSection>
  );
}
