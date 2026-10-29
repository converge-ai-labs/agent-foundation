import { useTranslation } from "react-i18next";

export function KnownSubtotal() {
  const { t } = useTranslation();
  return (
    <>
      <span aria-hidden="true">*</span>
      <span className="sr-only">
        {" "}
        {t("Known subtotal; some model calls are unpriced.")}
      </span>
    </>
  );
}
