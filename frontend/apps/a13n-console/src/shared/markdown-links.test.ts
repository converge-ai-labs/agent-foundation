import { expect, it } from "vitest";
import { markdownResourceLink } from "./markdown-links";

const origin = "https://console.example.com";
const page = `${origin}/workspace/ws_example/findings/fnd_example`;
const agent = "/workspace/ws_example/agents/agt_example";
const run =
  "/workspace/ws_example/sessions/ses_example/threads/thr_example/runs/run_example";

it.each([
  [agent, "agent", agent],
  [
    `${origin}${agent}?revision=rev_example#configuration`,
    "agent",
    `${agent}?revision=rev_example#configuration`,
  ],
  [`//console.example.com${agent}`, "agent", agent],
  ["../agents/agt_example", "agent", agent],
  [`${run}?view=chat#message`, "run", `${run}?view=chat#message`],
  [`${origin}${run}/`, "run", `${run}/`],
])(
  "recognizes resource destination %s without losing navigation state",
  (href, kind, to) => {
    expect(markdownResourceLink(href, page)).toEqual({ kind, to });
  },
);

it.each([
  undefined,
  "",
  "#evidence",
  "?revision=rev_example",
  `https://other.example.com${agent}`,
  `http://console.example.com${agent}`,
  `https://console.example.com:8443${agent}`,
  `https://console.example.com.evil.test${agent}`,
  `https://user:password@console.example.com${agent}`,
  "https://[invalid",
  "javascript:alert(1)",
  "mailto:agent@example.com",
  "/workspace/ws_example/agents",
  "/workspace/ws_example/agents/new",
  "/workspace/ws_example/agents/new/",
  `${agent}/edit`,
  "/workspace/ws_example/sessions/ses_example",
  "/api/v1/runs/run_example",
])(
  "leaves non-resource or external destination %s as an ordinary link",
  (href) => {
    expect(markdownResourceLink(href, page)).toBeNull();
  },
);

it("does not turn in-page anchors into resource links on Agent details", () => {
  expect(markdownResourceLink("#configuration", origin + agent)).toBeNull();
  expect(markdownResourceLink(agent, "not a URL")).toBeNull();
});
