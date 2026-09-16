import "a13n-ui/styles.css";
import { a13nLogoUrl } from "a13n-ui";
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserApp } from "./app";
import { InstallProvider } from "./shell/install";

document.querySelector<HTMLLinkElement>("#a13n-favicon")!.href = a13nLogoUrl;

const root = document.querySelector<HTMLElement>("#app");
if (root === null) throw new Error("Harness UI WebUI root element is missing");
createRoot(root).render(
  createElement(InstallProvider, { children: createElement(BrowserApp) }),
);
