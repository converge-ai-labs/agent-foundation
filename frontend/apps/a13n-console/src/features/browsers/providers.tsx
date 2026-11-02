import { useTranslation } from "react-i18next";
import { KindProviders } from "../providers/kind-providers";

export function BrowserProviders() {
  const { t } = useTranslation();
  return (
    <KindProviders
      kind="browser"
      addDescription="Choose where your browsers run."
      testDescription="Check the saved browser provider's availability without creating a browser."
      notice={
        <p className="text-sm text-muted-foreground">
          {t(
            "Docker is recommended for self-hosted browsers. Native runs on the Service host and is intended for Linux debugging.",
          )}
        </p>
      }
    />
  );
}
