import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
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

const TaskDialogContext = createContext<((open: boolean) => void) | null>(null);

/** Keep a dialog's owner mounted, but retire the menu when the dialog closes. */
export function useTaskOptionsDialog(open: boolean) {
  const onOpenChange = useContext(TaskDialogContext);
  useEffect(() => {
    if (!open || !onOpenChange) return;
    onOpenChange(true);
    return () => onOpenChange(false);
  }, [open, onOpenChange]);
}

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
  const [dialogOpen, setDialogOpen] = useState(false);
  const onDialogOpenChange = useCallback((next: boolean) => {
    if (next) setDialogOpen(true);
    else setOpen(false);
  }, []);
  const content = (
    <>
      <PopoverTitle className={styles.srOnly}>Chat options</PopoverTitle>
      <div className={styles.optionList}>
        <label
          className={styles.optionRow}
          aria-label="Goal"
          title="Keep working until the goal is done or the follow-up limit is reached."
        >
          <Target aria-hidden />
          <span>Goal</span>
          {goal}
        </label>
        {coordinator && (
          <label
            className={styles.optionRow}
            aria-label="Coordinate agents"
            title="Plan and delegate work to agents. Can't be turned off after sending."
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
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) setDialogOpen(false);
        setOpen(next);
      }}
    >
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
        data-dialog-open={dialogOpen || undefined}
        className={`${styles.popup} ${styles.taskPopup}`}
      >
        <TaskDialogContext value={onDialogOpenChange}>
          {content}
        </TaskDialogContext>
      </PopoverPopup>
    </Popover>
  );
}
