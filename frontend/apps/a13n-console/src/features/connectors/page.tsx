import { useTranslation } from "react-i18next";
import { Page } from "../../shared/feedback";
import { ConnectorConnections } from "./connections";

export function ConnectorsPage() {
  const { t } = useTranslation();
  return (
    <Page
      title={t("Connectors")}
      description={t(
        "Connect external accounts through your integration providers.",
      )}
    >
      <ConnectorConnections />
    </Page>
  );
}
