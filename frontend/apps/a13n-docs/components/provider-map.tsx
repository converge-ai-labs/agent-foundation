import Link from "next/link";
import type { ReactNode } from "react";
import { brands } from "a13n-ui/brand/brands";
import { markGeometry } from "a13n-ui/brand/mark";
import { type Locale, localeUrl } from "@/lib/i18n";
import styles from "./provider-map.module.css";

type Box = readonly [x: number, y: number, width: number, height: number];
type Entry = readonly [label: string, brand: string, href?: string];
type Region = {
  name: string;
  label: string;
  chinese: string;
  more: string;
  moreChinese: string;
  href: string;
  box: Box;
  columns: number;
  path: string;
  ports: readonly (readonly [number, number])[];
  entries: readonly Entry[];
};

const modelGuide = "/a13n-service/model-providers#model-providers";
const connectionGuide = "/a13n-service/tools#connections";
const environmentGuide = "/environments/providers";
const webGuide = "/a13n-service/tools#web-search-and-scrape";
const repository = "https://github.com/converge-ai-labs/agent-foundation";
const local = (locale: Locale, en: string, zh: string) =>
  locale === "en" ? en : zh;

const regions: Region[] = [
  {
    name: "models",
    label: "Models",
    chinese: "模型",
    more: "More models",
    moreChinese: "更多模型",
    href: modelGuide,
    box: [32, 132, 400, 330],
    columns: 3,
    path: "M432 252h16l12 12v78l12 12h6",
    ports: [
      [432, 252],
      [478, 354],
    ],
    entries: [
      ["OpenAI", "openai"],
      [
        "ChatGPT",
        "openai",
        "/a13n-service/model-providers#chatgpt-subscription-provider",
      ],
      ["Anthropic", "anthropic"],
      ["Gemini", "google_gemini"],
      ["Vertex AI", "google_vertex"],
      ["Azure OpenAI", "azure_openai"],
      ["AWS Bedrock", "aws_bedrock"],
      ["OpenRouter", "openrouter"],
      ["Fireworks AI", "fireworks"],
      ["Together AI", "together"],
      ["Cerebras", "cerebras"],
      ["SambaNova", "sambanova"],
      ["Vercel AI Gateway", "vercel"],
      ["xAI / Grok", "xai"],
      ["Ollama", "ollama"],
      ["Alibaba Cloud", "alibaba_model_studio"],
      ["DeepSeek", "deepseek"],
      ["Moonshot / Kimi", "kimi"],
      ["MiniMax", "minimax"],
      ["Zhipu / GLM", "zhipu"],
      ["TypeSafe", "typesafe"],
    ],
  },
  {
    name: "connections",
    label: "Connections",
    chinese: "应用连接",
    more: "More connections",
    moreChinese: "更多应用连接",
    href: connectionGuide,
    box: [32, 488, 400, 138],
    columns: 3,
    path: "M432 570h16l12-12V420l12-12h6",
    ports: [
      [432, 570],
      [478, 408],
    ],
    entries: [
      ["GitHub", "github"],
      ["Notion", "notion"],
      ["Linear", "linear"],
      ["Sentry", "sentry"],
      ["Stripe", "stripe"],
      ["Cloudflare", "cloudflare"],
    ],
  },
  {
    name: "environments",
    label: "Environments",
    chinese: "执行环境",
    more: "More environments",
    moreChinese: "更多执行环境",
    href: environmentGuide,
    box: [784, 132, 384, 181],
    columns: 3,
    path: "M734 354h10l12-12V258l12-12h16",
    ports: [
      [734, 354],
      [784, 246],
    ],
    entries: [
      ["E2B", "e2b", "/environments/cloud-providers#e2b-runtime"],
      ["Daytona", "daytona", "/environments/cloud-providers#daytona"],
      ["Runloop", "runloop", "/environments/cloud-providers#runloop"],
      ["Docker", "docker", "/environments/providers#docker-runtime"],
      ["Modal", "modal", "/environments/cloud-providers#modal"],
      ["Vercel", "vercel", "/environments/cloud-providers#vercel-sandbox"],
      [
        "Fly.io Sprites",
        "flyio",
        "/environments/cloud-providers#flyio-sprites",
      ],
      ["Local", "local", "/environments/providers#local-envd-runtime"],
      ["Envd", "envd", "/environments/remote-envd"],
    ],
  },
  {
    name: "web",
    label: "Web Data",
    chinese: "网页搜索与抓取",
    more: "More web tools",
    moreChinese: "更多网页工具",
    href: webGuide,
    box: [784, 338, 384, 288],
    columns: 2,
    path: "M734 408h10l12 12v86l12 12h16",
    ports: [
      [734, 408],
      [784, 518],
    ],
    entries: [
      ["Exa", "exa"],
      ["Tavily", "tavily"],
      ["Firecrawl", "firecrawl"],
      ["Jina", "jina"],
      ["Parallel", "parallel"],
      ["TinyFish", "tinyfish"],
      ["Brave Search", "brave"],
      ["Perplexity", "perplexity"],
      ["SerpApi", "serpapi"],
      ["DuckDuckGo", "duckduckgo"],
      ["DeepSeek", "deepseek"],
    ],
  },
];

function Target({
  href,
  name,
  locale,
  box,
  children,
}: {
  href: string;
  name: string;
  locale: Locale;
  box: Box;
  children: ReactNode;
}) {
  const [x, y, width, height] = box;
  return (
    <Link
      href={localeUrl(href, locale)}
      aria-label={name}
      className={styles.link}
    >
      <title>{name}</title>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx="4"
        className={styles.hit}
      />
      {children}
    </Link>
  );
}

function Frame({ box }: { box: Box }) {
  const [x, y, width, height] = box;
  return (
    <path
      d={`M${x} ${y}h${width - 14}l14 14v${height - 14}h-${width}z`}
      className={styles.frame}
    />
  );
}

function CategoryIcon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    models: <path d="m2 8 10-5 10 5-10 5Zm1 5 9 4.5 9-4.5m-18 4 9 4.5 9-4.5" />,
    connections: (
      <path d="m9 15 6-6m-6 9-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m0 6a4 4 0 0 0 6 0l5-5a4 4 0 0 0-6-6l-2 2" />
    ),
    environments: <path d="m3 6 9-4 9 4v12l-9 4-9-4Zm0 0 9 5 9-5m-9 5v11" />,
    web: (
      <>
        <ellipse cx="12" cy="5" rx="9" ry="3" />
        <path d="M3 5v14c0 4 18 4 18 0V5M3 10c0 4 18 4 18 0M3 15c0 4 18 4 18 0" />
      </>
    ),
  };
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
      aria-hidden
    >
      {paths[name]}
    </svg>
  );
}

function ProviderIcon({
  brand,
  x,
  y,
}: {
  brand: string;
  x: number;
  y: number;
}) {
  if (brand === "local" || brand === "envd") {
    return (
      <svg
        x={x}
        y={y}
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden
      >
        <rect x="2" y="3" width="20" height="15" rx="1" />
        <path
          d={brand === "local" ? "M12 18v4m-6 0h12" : "m6 7 4 4-4 4m7 0h5"}
        />
      </svg>
    );
  }
  // Next imports local SVGs as static images; the shared registry also serves Vite.
  const icon = brands[brand].icon;
  const src = typeof icon === "string" ? icon : (icon as { src: string }).src;
  return <image href={src} x={x} y={y} width="22" height="22" aria-hidden />;
}

function ProviderRegion({
  region,
  locale,
}: {
  region: Region;
  locale: Locale;
}) {
  const [x, y, width] = region.box;
  const cellWidth = (width - 24) / region.columns;
  return (
    <g className={`${styles.region} ${styles[region.name]}`}>
      <Frame box={region.box} />
      <path
        d={`M${x + 16} ${y + 43}h${width - 32}`}
        className={styles.divider}
      />
      <Target
        href={region.href}
        name={local(locale, region.label, region.chinese)}
        locale={locale}
        box={[x + 10, y + 8, width - 50, 30]}
      >
        <g transform={`translate(${x + 18} ${y + 12})`}>
          <CategoryIcon name={region.name} />
        </g>
        <text x={x + 52} y={y + 31} className={styles.category}>
          {local(locale, region.label, region.chinese)}
        </text>
      </Target>
      <Target
        href={region.href}
        name={local(locale, region.more, region.moreChinese)}
        locale={locale}
        box={[x + width - 40, y + 8, 30, 30]}
      >
        <text
          x={x + width - 25}
          y={y + 29}
          textAnchor="middle"
          className={styles.more}
        >
          …
        </text>
      </Target>
      {region.entries.map(([label, brand, href], index) => {
        const left = x + 12 + (index % region.columns) * cellWidth;
        const top = y + 54 + Math.floor(index / region.columns) * 36;
        return (
          <Target
            key={label}
            href={href ?? region.href}
            name={label}
            locale={locale}
            box={[left, top, cellWidth - 2, 32]}
          >
            <ProviderIcon brand={brand} x={left + 4} y={top + 5} />
            {label === "Vercel AI Gateway" ? (
              <>
                <text x={left + 34} y={top + 14} className={styles.provider}>
                  Vercel
                </text>
                <text x={left + 34} y={top + 27} className={styles.detail}>
                  AI Gateway
                </text>
              </>
            ) : (
              <text x={left + 34} y={top + 21} className={styles.provider}>
                {label}
              </text>
            )}
          </Target>
        );
      })}
      <g className={styles.connection} aria-hidden>
        <path d={region.path} />
        {region.ports.map(([px, py]) => (
          <rect
            key={`${px}-${py}`}
            x={px - 3}
            y={py - 3}
            width="6"
            height="6"
          />
        ))}
      </g>
    </g>
  );
}

export function ProviderMap({
  alt,
  locale = "en",
}: {
  alt: string;
  locale?: Locale;
}) {
  return (
    <figure className={styles.overview}>
      <figcaption className={styles.hint}>
        {local(
          locale,
          "Scroll horizontally to explore. Select a component to open its docs.",
          "左右滑动查看，点击组件打开对应文档。",
        )}
      </figcaption>
      <div
        className={styles.scroll}
        role="region"
        aria-label={alt}
        tabIndex={0}
      >
        <svg
          viewBox="0 0 1200 704"
          className={styles.canvas}
          role="group"
          aria-label={alt}
        >
          <defs>
            <pattern
              id="provider-grid"
              width="12"
              height="12"
              patternUnits="userSpaceOnUse"
            >
              <circle cx="1" cy="1" r="0.7" fill="#cec9ef" />
            </pattern>
            <radialGradient id="provider-grid-fade">
              <stop stopColor="white" />
              <stop offset="1" stopColor="white" stopOpacity="0" />
            </radialGradient>
            <mask id="provider-grid-mask">
              <rect
                x="438"
                y="140"
                width="340"
                height="480"
                fill="url(#provider-grid-fade)"
              />
            </mask>
          </defs>
          <text x="36" y="64" className={styles.headline}>
            {local(locale, "Bring your own stack", "自由选择你的技术栈")}
            <tspan fill="#5036d3">.</tspan>
          </text>
          <text x="36" y="96" className={styles.subtitle}>
            {local(
              locale,
              "Build agents. Run reliably at scale on your cloud.",
              "构建 agent，在自己的云上稳定运行并扩展规模。",
            )}
          </text>
          <rect
            x="438"
            y="140"
            width="340"
            height="480"
            fill="url(#provider-grid)"
            mask="url(#provider-grid-mask)"
            aria-hidden
          />
          <Frame box={[478, 280, 256, 185]} />
          <Target
            href="/a13n-service/get-started"
            name={local(locale, "Self-host a13n", "自行部署 a13n")}
            locale={locale}
            box={[484, 286, 244, 173]}
          >
            <text x="496" y="307" className={styles.cloud}>
              {local(locale, "Your Cloud", "你的云环境")}
            </text>
            <path d="M496 326h200l20 20v65H496z" className={styles.processor} />
            <svg
              x="513"
              y="346"
              width="49"
              height="49"
              viewBox={markGeometry.viewBox}
              aria-hidden
            >
              {markGeometry.angles.map((angle) => (
                <rect
                  key={angle}
                  {...markGeometry.bar}
                  transform={`rotate(${angle} 512 512)`}
                  fill="white"
                />
              ))}
              {markGeometry.angles.map((angle) => (
                <rect
                  key={angle}
                  {...markGeometry.cutout}
                  transform={`rotate(${angle} 512 512)`}
                  className={styles.processor}
                />
              ))}
            </svg>
            <text x="570" y="388" className={styles.wordmark}>
              a13n
            </text>
            <text
              x="606"
              y="440"
              textAnchor="middle"
              className={styles.caption}
            >
              {local(locale, "Open source & Self-hosted", "开源 · 自行部署")}
            </text>
          </Target>
          {regions.map((region) => (
            <ProviderRegion key={region.name} region={region} locale={locale} />
          ))}
          <path d="M32 660h1136" className={styles.divider} />
          <Target
            href={repository}
            name={local(locale, "GitHub repository", "GitHub 仓库")}
            locale={locale}
            box={[820, 667, 348, 30]}
          >
            <ProviderIcon brand="github" x={830} y={671} />
            <text x="862" y="687" className={styles.footer}>
              converge-ai-labs/agent-foundation
            </text>
          </Target>
        </svg>
      </div>
    </figure>
  );
}
