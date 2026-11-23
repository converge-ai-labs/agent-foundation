import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { ErrorToast } from "../../shared/feedback";
import { useAgentComposer, type ComposerTarget } from "./composer";

export function ComposerAction({ target }: { target: ComposerTarget }) {
  const { can } = useWorkspace();
  return can("run") ? <AvailableComposerAction target={target} /> : null;
}

function AvailableComposerAction({ target }: { target: ComposerTarget }) {
  const composer = useAgentComposer();
  const { t } = useTranslation();
  return (
    <>
      <Button
        type="button"
        variant="outline"
        disabled={!composer.available}
        loading={composer.pending}
        onClick={() => composer.start(target)}
      >
        {t("Ask Composer")}
      </Button>
      {composer.setup}
      <ErrorToast error={composer.error} />
    </>
  );
}
