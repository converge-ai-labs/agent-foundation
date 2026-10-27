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
  environment,
  context,
}: {
  goal: ReactNode;
  coordinator?: ReactNode;
  environment?: ReactNode;
  context?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const content = (
    <>
      <PopoverTitle className={styles.srOnly}>Chat options</PopoverTitle>
      <div className={styles.optionList}>
        <label className={styles.optionRow} aria-label="Keep working">
          <Target aria-hidden />
          <span className={styles.optionCopy}>
            <span>Keep working</span>
            <small>Continue until your goal is complete</small>
          </span>
          {goal}
        </label>
        {coordinator && (
          <label className={styles.optionRow} aria-label="Coordinate agents">
            <TreeStructure aria-hidden />
            <span className={styles.optionCopy}>
              <span>Coordinate agents</span>
              <small>Can't be turned off after sending</small>
            </span>
            {coordinator}
          </label>
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
        sideOffset={8}
        className={`${styles.popup} ${styles.taskPopup}`}
      >
        {content}
      </PopoverPopup>
    </Popover>
  );
}
