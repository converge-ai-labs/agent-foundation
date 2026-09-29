"use client";
import type { MediaAdapter } from "fumadocs-openapi";
import { createCodeUsageGeneratorRegistry } from "fumadocs-openapi/requests/generators";
import { curl } from "fumadocs-openapi/requests/generators/curl";
import { javascript } from "fumadocs-openapi/requests/generators/javascript";
import { python } from "fumadocs-openapi/requests/generators/python";
import { createOpenAPIPage } from "fumadocs-openapi/ui";

// Image uploads (agent avatars) send the file bytes as the request body.
const imageBody: MediaAdapter = {
  encode: ({ body }) => body as BodyInit,
  generateExample(_data, { lang }) {
    if (lang === "python") return 'body = open("avatar.png", "rb").read()';
    if (lang === "js")
      return 'const body = await (await fetch("avatar.png")).blob();';
    return undefined;
  },
};

const codeUsages = createCodeUsageGeneratorRegistry();
codeUsages.add("curl", curl);
codeUsages.add("python", python);
codeUsages.add("js", javascript);

// The static site has no request proxy, so the playground is off; examples stay copyable.
export const APIPage = createOpenAPIPage({
  playground: { enabled: false },
  codeUsages,
  mediaAdapters: {
    "image/png": imageBody,
    "image/jpeg": imageBody,
    "image/webp": imageBody,
  },
});
