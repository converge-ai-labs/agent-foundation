import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  Button,
  Dialog,
  DialogClose,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
  useMediaQuery,
} from "a13n-ui";
import {
  ArrowLeft,
  ArrowRight,
  CaretDown,
  CaretRight,
} from "@phosphor-icons/react";
import styles from "./execution-details.module.css";
import conversation from "./conversation.module.css";

// Opening inspection exits conversation following even without a pointer gesture.
export const PauseConversationFollowing = createContext<
  (() => void) | undefined
>(undefined);

export function ExecutionDetails({
  complete,
  summary,
  preview,
  children,
}: {
  complete: boolean;
  summary: ReactNode;
  preview: string;
  children: (open: boolean) => ReactNode;
}) {
  const mobile = useMediaQuery("(max-width: 700px)");
  const pauseFollowing = useContext(PauseConversationFollowing);
  const [expanded, setExpanded] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [visited, setVisited] = useState(false);
  const open = expanded;
  useEffect(() => {
    if (!mobile) setInspecting(false);
  }, [mobile]);

  if (!mobile)
    return (
      <div className={conversation.execution}>
        <button
          type="button"
          aria-expanded={open}
          className={conversation.executionToggle}
          onClick={() => {
            if (!open) {
              pauseFollowing?.();
              setVisited(true);
            }
            setExpanded(!open);
          }}
        >
          <span className={conversation.executionTitle}>
            {open ? (
              <CaretDown aria-hidden="true" />
            ) : (
              <CaretRight aria-hidden="true" />
            )}
            Execution details
          </span>
          {summary}
        </button>
        {visited && children(open)}
      </div>
    );

  return (
    <Dialog
      open={inspecting}
      onOpenChange={(next) => {
        setInspecting(next);
        if (next) {
          pauseFollowing?.();
          setVisited(true);
        }
      }}
    >
      <DialogTrigger className={styles.preview}>
        <span className={styles.previewHeading}>
          <span>Execution details{summary}</span>
          <ArrowRight aria-hidden="true" />
        </span>
        <span className={styles.excerpt}>{preview}</span>
      </DialogTrigger>
      {visited && (
        <DialogPopup
          className={styles.fullscreen}
          portalProps={{ keepMounted: true }}
          showCloseButton={false}
          // Keep the reader and inner disclosures mounted between visits.
          hidden={!inspecting}
        >
          <header className={styles.header}>
            <DialogClose
              render={<Button variant="ghost" size="icon" />}
              aria-label="Back to conversation"
            >
              <ArrowLeft />
            </DialogClose>
            <div>
              <DialogTitle className={styles.title}>
                Execution details
              </DialogTitle>
              <div className={styles.metrics}>
                {complete ? "Completed" : "Execution history"}
                {summary}
              </div>
            </div>
          </header>
          {children(inspecting)}
        </DialogPopup>
      )}
    </Dialog>
  );
}
