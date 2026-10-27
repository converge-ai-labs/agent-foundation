import "a13n-ui/styles.css";
import "./shell/disclosure.css";
import "./shell/compact-controls.css";
import "./shell/theme.css";
import { a13nLogoUrl } from "a13n-ui";
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserApp } from "./app";
import { InstallProvider } from "./shell/install";
import { readPreference } from "./shell/preferences";
import { desktopPlatform, isDesktop } from "./platform";

// Apply the same theme to startup and authenticated content, before first paint.
document.documentElement.classList.toggle(
  "dark",
  readPreference("theme", "light") === "dark",
);

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
