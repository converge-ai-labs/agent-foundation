import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserApp } from "./app";
import "./style.css";

const root = document.querySelector<HTMLElement>("#app");
if (root === null) throw new Error("Harness UI root element is missing");
createRoot(root).render(createElement(BrowserApp));
