import { useState, type ReactNode } from "react";
import {
  Button,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "a13n-ui";
import { SlidersHorizontal, X } from "@phosphor-icons/react";
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
      <div className={styles.header}>
        <PopoverTitle className={styles.title}>Task options</PopoverTitle>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close task options"
          onClick={() => setOpen(false)}
        >
          <X />
        </Button>
      </div>
      <div className={styles.body}>
        <div className={styles.optionRow}>
          <div>
            <strong>Goal mode</strong>
            <p>Keep working and checking until the objective is complete.</p>
          </div>
          {goal}
        </div>
        {coordinator && (
          <div className={styles.optionRow}>
            <div>
              <strong>Coordinator</strong>
              <p>
                Plan and delegate work in this project. Permanent after sending.
              </p>
            </div>
            {coordinator}
          </div>
        )}
        {environment && (
          <div className={styles.optionRow}>
            <div>
              <strong>Working environment</strong>
              <p>Choose where this chat runs and which folders it can use.</p>
            </div>
            {environment}
          </div>
        )}
        {context && (
          <div className={styles.optionRow}>
            <div>
              <strong>Start with fresh context</strong>
              <p>Keep your chat history, files and draft.</p>
            </div>
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
        aria-label="Task options"
        title="Task options"
      >
        <SlidersHorizontal />
      </PopoverTrigger>
      <PopoverPopup side="top" align="start" className={styles.popup}>
        {content}
      </PopoverPopup>
    </Popover>
  );
}
