import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserApp } from "./app";
import "./styles.css";

document.body.classList.add("a13n-root");

const root = document.querySelector<HTMLElement>("#app");
if (root === null) throw new Error("Harness UI WebUI root element is missing");
createRoot(root).render(createElement(BrowserApp));
