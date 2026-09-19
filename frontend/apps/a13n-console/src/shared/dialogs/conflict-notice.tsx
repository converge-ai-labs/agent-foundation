import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
  Button,
} from "a13n-ui";
import { ArrowsClockwiseIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";

export interface ConflictAction {
  label: string;
  onClick: () => void;
  pending?: boolean;
}

/**
 * Concurrency recovery: a saved resource moved underneath the draft (412), or a
 * create could not be confirmed. One notice, one explanation, and the two ways
 * out — reconcile with what the server holds, or continue with the draft.
 */
export function ConflictNotice({
  title,
  description,
  recover,
  proceed,
  children,
}: {
  title: string;
  description: string;
  recover: ConflictAction;
  proceed?: ConflictAction;
  children?: ReactNode;
}) {
  return (
    <Alert variant="warning" className="my-4">
      <ArrowsClockwiseIcon aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{description}</p>
        {children}
      </AlertDescription>
      <AlertAction>
        <Button
          type="button"
          size="sm"
          variant="outline"
          loading={recover.pending}
          onClick={recover.onClick}
        >
          {recover.label}
        </Button>
        {proceed && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            loading={proceed.pending}
            onClick={proceed.onClick}
          >
            {proceed.label}
          </Button>
        )}
      </AlertAction>
    </Alert>
  );
}
