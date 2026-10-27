import { useState, type ReactNode } from "react";
import {
  Button,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "a13n-ui";
import {
  SlidersHorizontal,
  Target,
  TreeStructure,
} from "@phosphor-icons/react";
import styles from "./composer-settings.module.css";

/** Infrequent work choices stay together, without taking space from the draft. */
export function TaskOptions({
  goal,
  coordinator,
  coordinatorEnabled = false,
  environment,
  context,
}: {
  goal: ReactNode;
  coordinator?: ReactNode;
  coordinatorEnabled?: boolean;
  environment?: ReactNode;
  context?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const content = (
    <>
      <PopoverTitle className={styles.srOnly}>Chat options</PopoverTitle>
      <div className={styles.optionList}>
        <label
          className={styles.optionRow}
          aria-label="Keep working"
          title="Continue until your goal is complete or the continuation limit is reached."
        >
          <Target aria-hidden />
          <span>Keep working</span>
          {goal}
        </label>
        {coordinator && (
          <label
            className={styles.optionRow}
            aria-label="Coordinate agents"
            title="Plan and delegate work across agents. This cannot be turned off after sending."
          >
            <TreeStructure aria-hidden />
            <span>Coordinate agents</span>
            {coordinator}
          </label>
        )}
        {coordinator && coordinatorEnabled && (
          <p className={styles.optionHint}>
            Agent coordination stays on after sending.
          </p>
        )}
        {(environment || context) && (
          <div className={styles.optionActions}>
            {environment}
            {context}
          </div>
        )}
      </div>
    </>
  );
  const trigger = <Button variant="ghost" size="icon-sm" />;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={trigger}
        aria-label="Chat options"
        title="Chat options"
      >
        <SlidersHorizontal />
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="end"
        sideOffset={6}
        className={`${styles.popup} ${styles.taskPopup}`}
      >
        {content}
      </PopoverPopup>
    </Popover>
  );
}
