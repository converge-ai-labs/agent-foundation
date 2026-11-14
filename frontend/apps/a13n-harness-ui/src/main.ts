import "a13n-ui/styles.css";
import "./shell/disclosure.css";
import "./shell/compact-controls.css";
import "./shell/theme.css";
import { a13nLogoUrl } from "a13n-ui";
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { i18n } from "./i18n";
import { BrowserApp } from "./app";
import { fitVisualViewport } from "./shell/visual-viewport";
import { InstallProvider } from "./shell/install";
import { appearanceStore } from "./appearance/store";
import { desktopPlatform, isDesktop } from "./platform";

document.documentElement.lang = i18n.resolvedLanguage ?? "en";

// Apply the same theme to startup and authenticated content, before first paint.
appearanceStore().start();

// Body portals share the same keyboard-aware bounds as the workbench.
fitVisualViewport(document.documentElement);

// Browser chrome follows the chosen app theme, not just the OS preference.
function syncThemeColor() {
  const color = getComputedStyle(document.documentElement)
    .getPropertyValue("--background")
    .trim();
  for (const meta of document.querySelectorAll<HTMLMetaElement>(
    'meta[name="theme-color"]',
  )) {
    meta.content = color;
  }
}
syncThemeColor();
new MutationObserver(syncThemeColor).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ["class"],
});

// Desktop shells own the window chrome; styles key off these attributes.
if (isDesktop()) {
  document.documentElement.dataset.desktop = desktopPlatform() ?? "";
}

document.querySelector<HTMLLinkElement>("#a13n-favicon")!.href = a13nLogoUrl;

const root = document.querySelector<HTMLElement>("#app");
if (root === null) throw new Error("Harness UI WebUI root element is missing");
createRoot(root).render(
  createElement(InstallProvider, { children: createElement(BrowserApp) }),
);
