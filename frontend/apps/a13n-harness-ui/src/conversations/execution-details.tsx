import { ReaderFades } from "./reader-fades";
import { useTranslation } from "../i18n";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  Button,
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  Dialog,
  DialogClose,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
  useMediaQuery,
} from "a13n-ui";
import { ArrowLeft, ArrowRight, CaretRight } from "@phosphor-icons/react";
import styles from "./execution-details.module.css";
import conversation from "./conversation.module.css";

// Opening inspection exits conversation following even without a pointer gesture.
export const PauseConversationFollowing = createContext<
  (() => void) | undefined
>(undefined);

export function ExecutionDetails({
  complete,
  preview,
  running,
  children,
}: {
  complete: boolean;
  preview: string;
  running?: boolean;
  children: (open: boolean) => ReactNode;
}) {
  const { t } = useTranslation();
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
      <Collapsible
        className={conversation.execution}
        open={expanded}
        onOpenChange={(next) => {
          if (next) {
            pauseFollowing?.();
            setVisited(true);
          }
          setExpanded(next);
        }}
      >
        <div className={conversation.executionHeading}>
          <CollapsibleTrigger className={conversation.executionToggle}>
            {preview && (
              <span
                className={conversation.executionPreview}
                data-running-label={running || undefined}
                title={preview}
              >
                {preview}
              </span>
            )}
            <span className={conversation.srOnly}>{t("Work log")}</span>
            <CaretRight
              className={conversation.executionCaret}
              aria-hidden="true"
            />
          </CollapsibleTrigger>
        </div>
        <CollapsiblePanel
          className={styles.readerPanel}
          keepMounted
          inert={!open}
        >
          {visited && children(open)}
          <ReaderFades />
        </CollapsiblePanel>
      </Collapsible>
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
      <DialogTrigger className={`${styles.preview} ${conversation.execution}`}>
        <span className={styles.previewHeading}>
          <span
            className={styles.excerpt}
            data-running-label={running || undefined}
          >
            {preview}
          </span>
          <span className={conversation.srOnly}>{t("Work log")}</span>
          <ArrowRight aria-hidden="true" />
        </span>
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
              aria-label={t("Back to chat")}
            >
              <ArrowLeft />
            </DialogClose>
            <div>
              <DialogTitle className={styles.title}>
                {preview || t("Recorded steps")}
              </DialogTitle>
              <div className={styles.metrics}>
                {complete ? t("Recorded steps") : t("Steps so far")}
              </div>
            </div>
          </header>
          {children(inspecting)}
        </DialogPopup>
      )}
    </Dialog>
  );
}
