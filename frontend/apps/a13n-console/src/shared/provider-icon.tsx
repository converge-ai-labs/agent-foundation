import {
  CubeIcon,
  DesktopIcon,
  TerminalWindowIcon,
  GlobeIcon,
  ArrowsLeftRightIcon,
} from "@phosphor-icons/react";
import { useState } from "react";

const icons: Record<string, string> = {
  brave: "brave-color",
  exa: "exa-color",
  openai: "openai",
  anthropic: "anthropic",
  google_gemini: "gemini-color",
  google_vertex: "vertexai-color",
  azure_openai: "azure-color",
  aws_bedrock: "bedrock-color",
  openrouter: "openrouter-color",
  ollama: "ollama",
  alibaba_model_studio: "qwen-color",
  deepseek: "deepseek-color",
  moonshot: "kimi-color",
  zhipu: "zhipu-color",
};

export function ProviderIcon({ type }: { type: string }) {
  const [failed, setFailed] = useState<string>();
  const localIcons = {
    "a13n.direct-local": DesktopIcon,
    "a13n.local-envd": TerminalWindowIcon,
    "a13n.http-envd": GlobeIcon,
    "a13n.websocket-envd": ArrowsLeftRightIcon,
  };
  const LocalIcon = localIcons[type as keyof typeof localIcons];
  if (LocalIcon)
    return (
      <LocalIcon
        aria-hidden
        className="size-5 shrink-0 text-muted-foreground"
      />
    );
  const externalIcons: Record<string, string> = {
    "a13n.docker":
      "https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/docker/docker-original.svg",
    composio: "https://composio.dev/logos/composio-black.svg",
    openconnector:
      "https://cdn.jsdelivr.net/gh/oomol-lab/open-connector@4d7d59de1f6d474a1afb6194de008a0affc08b10/web/src/assets/oomol-connect-logo.png",
    "a13n.e2b": "https://e2b.dev/brand/e2b-symbol-fire-orange-s.svg",
  };
  const external = externalIcons[type];
  const icon = icons[type];
  const src =
    external ??
    (icon
      ? `https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.94.0/icons/${icon}.svg`
      : undefined);
  if (!src || failed === src)
    return <CubeIcon aria-hidden className="size-5 shrink-0" />;
  return (
    <img
      // Fixed versions use the CDN's immutable, year-long browser cache.
      src={src}
      alt=""
      width={20}
      height={20}
      className={`size-5 shrink-0 object-contain ${(external && type !== "composio" && type !== "openconnector") || icon?.endsWith("-color") ? "" : "dark:invert"}`}
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(src)}
    />
  );
}
