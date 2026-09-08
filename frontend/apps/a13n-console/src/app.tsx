import { useTranslation } from "react-i18next";

export function App() {
  const { t } = useTranslation();
  return <h1>{t("welcome")}</h1>;
}
