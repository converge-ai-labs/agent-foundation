import { DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { CredentialSessions } from "./sessions";

/** Inspect stored evidence only while this section is open. */
export function EnvironmentCredentials({
  environmentId,
}: {
  environmentId: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <DisclosureSection
      title={t("Credentials")}
      open={open}
      onOpenChange={setOpen}
    >
      {open && <CredentialSessions target={{ environmentId }} />}
    </DisclosureSection>
  );
}
