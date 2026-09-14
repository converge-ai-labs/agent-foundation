import "a13n-ui/styles.css";
import { a13nLogoUrl } from "a13n-ui";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import { App } from "./app";
import { i18n } from "./i18n";

document.querySelector<HTMLLinkElement>("#a13n-favicon")!.href = a13nLogoUrl;

createRoot(document.getElementById("root")!).render(
  <I18nextProvider i18n={i18n}>
    <App />
  </I18nextProvider>,
);
