import { Collapsible, CollapsiblePanel } from "a13n-ui";
import { CaretRightIcon } from "@phosphor-icons/react";
import { useId, type ReactNode } from "react";
import { SectionGroup } from "../../../shared/page";
import styles from "./editor.module.css";

/** Use the same title/content columns as the other editor sections. */
export function ConfigurationGroup({
  title,
  description,
  open,
  onOpenChange,
  children,
}: {
  title: string;
  description: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
}) {
  const panelId = useId();
  return (
    <SectionGroup
      description={description}
      title={
        <button
          type="button"
          className={styles.mediaTrigger}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => onOpenChange(!open)}
        >
          {title}
          <CaretRightIcon size={14} aria-hidden="true" />
        </button>
      }
    >
      <Collapsible open={open} onOpenChange={onOpenChange}>
        <CollapsiblePanel id={panelId}>
          <div className={styles.advancedBody}>{children}</div>
        </CollapsiblePanel>
      </Collapsible>
    </SectionGroup>
  );
}
