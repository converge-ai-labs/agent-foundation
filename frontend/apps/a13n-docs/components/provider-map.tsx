import Image from "next/image";
import Link from "next/link";
import providers from "../../../../.github/assets/a13n-architecture.webp";
import { type Locale, localeUrl } from "@/lib/i18n";
import styles from "./provider-map.module.css";

type Hotspot = {
  label: string;
  chinese?: string;
  href: string;
  // Coordinates on the original artwork, normalized to a 2000 × 1250 canvas.
  bounds: readonly [x: number, y: number, width: number, height: number];
};

type Region = {
  name: string;
  path?: string;
  links: Hotspot[];
};

const modelGuide = "/a13n-service/model-providers#model-providers";
const connectionGuide = "/a13n-service/tools#connections";
const environmentGuide = "/environments/providers";
const webGuide = "/a13n-service/tools#web-search-and-scrape";

// Each row follows the logo order in the artwork. A tuple links to a more specific guide.
type Entry = string | readonly [label: string, href: string];
function gridLinks(
  rows: Entry[][],
  columns: number[],
  top: number,
  width: number,
  defaultHref: string,
): Hotspot[] {
  return rows.flatMap((row, y) =>
    row.map((entry, x): Hotspot => {
      const [label, href] =
        typeof entry === "string" ? [entry, defaultHref] : entry;
      return { label, href, bounds: [columns[x], top + y * 62.5, width, 58] };
    }),
  );
}

const leftColumns = [82, 296, 510];
const regions: Region[] = [
  {
    name: "models",
    path: "M724 615 Q737 615 737 630 T750 646",
    links: [
      {
        label: "Models",
        chinese: "模型",
        href: modelGuide,
        bounds: [82, 321, 130, 54],
      },
      ...gridLinks(
        [
          [
            "OpenAI",
            [
              "ChatGPT",
              "/a13n-service/model-providers#chatgpt-subscription-provider",
            ],
            "Anthropic",
          ],
          ["Gemini", "Vertex AI", "Azure OpenAI"],
          ["AWS Bedrock", "OpenRouter", "Fireworks AI"],
          ["Together AI", "Cerebras", "SambaNova"],
          ["Vercel AI Gateway", "xAI / Grok", "Ollama"],
          ["Alibaba Cloud", "DeepSeek", "Moonshot / Kimi"],
          ["MiniMax", "Zhipu / GLM", "TypeSafe"],
        ],
        leftColumns,
        400,
        173,
        modelGuide,
      ),
      {
        label: "More models",
        chinese: "更多模型",
        href: modelGuide,
        bounds: [683, 780, 40, 52],
      },
    ],
  },
  {
    name: "connections",
    path: "M724 1022 Q737 1022 737 1008 V785 Q737 770 750 770",
    links: [
      {
        label: "Connections",
        chinese: "应用连接",
        href: connectionGuide,
        bounds: [82, 885, 180, 50],
      },
      ...gridLinks(
        [
          ["GitHub", "Notion", "Linear"],
          ["Sentry", "Stripe", "Cloudflare"],
        ],
        leftColumns,
        962,
        173,
        connectionGuide,
      ),
      {
        label: "More connections",
        chinese: "更多应用连接",
        href: connectionGuide,
        bounds: [683, 1025, 40, 55],
      },
    ],
  },
  {
    name: "environments",
    path: "M1250 646 Q1263 646 1263 631 V506 Q1263 490 1276 490",
    links: [
      {
        label: "Environments",
        chinese: "执行环境",
        href: environmentGuide,
        bounds: [1282, 321, 220, 54],
      },
      ...gridLinks(
        [
          [
            ["E2B", "/environments/cloud-providers#e2b-runtime"],
            ["Daytona", "/environments/cloud-providers#daytona"],
            ["Runloop", "/environments/cloud-providers#runloop"],
          ],
          [
            ["Docker", "/environments/providers#docker-runtime"],
            ["Modal", "/environments/cloud-providers#modal"],
            ["Vercel", "/environments/cloud-providers#vercel-sandbox"],
          ],
          [
            ["Fly.io Sprites", "/environments/cloud-providers#flyio-sprites"],
            ["Local", "/environments/providers#local-envd-runtime"],
            ["Envd", "/environments/remote-envd"],
          ],
        ],
        [1282, 1497, 1711],
        400,
        170,
        environmentGuide,
      ),
      {
        label: "More environments",
        chinese: "更多执行环境",
        href: environmentGuide,
        bounds: [1882, 525, 42, 55],
      },
    ],
  },
  {
    name: "web",
    path: "M1250 770 Q1263 770 1263 785 V882 Q1263 896 1276 896",
    links: [
      {
        label: "Web Data",
        chinese: "网页搜索与抓取",
        href: webGuide,
        bounds: [1282, 635, 150, 51],
      },
      ...gridLinks(
        [
          ["Exa", "Tavily"],
          ["Firecrawl", "Jina"],
          ["Parallel", "TinyFish"],
          ["Brave Search", "Perplexity"],
          ["SerpApi", "DuckDuckGo"],
          ["DeepSeek"],
        ],
        [1282, 1603],
        712,
        230,
        webGuide,
      ),
      {
        label: "More web tools",
        chinese: "更多网页工具",
        href: webGuide,
        bounds: [1882, 1025, 42, 55],
      },
    ],
  },
  {
    name: "foundation",
    links: [
      {
        label: "Self-host a13n",
        chinese: "自行部署 a13n",
        href: "/a13n-service/get-started",
        bounds: [752, 553, 494, 308],
      },
      {
        label: "GitHub repository",
        chinese: "GitHub 仓库",
        href: "https://github.com/converge-ai-labs/agent-foundation",
        bounds: [1625, 1165, 296, 57],
      },
    ],
  },
];

export function ProviderMap({
  alt,
  locale = "en",
}: {
  alt: string;
  locale?: Locale;
}) {
  const hint =
    locale === "en"
      ? "Scroll horizontally to explore. Select a component to open its docs."
      : "左右滑动查看，点击组件打开对应文档。";

  return (
    <figure className={styles.overview}>
      <figcaption className={styles.hint}>{hint}</figcaption>
      <div
        className={styles.scroll}
        role="region"
        aria-label={alt}
        tabIndex={0}
      >
        <div className={styles.canvas}>
          <Image src={providers} alt={alt} className={styles.artwork} />
          {regions.map((region) => (
            <div key={region.name} className={styles.region}>
              {region.path && (
                <svg
                  className={styles.connection}
                  viewBox="0 0 2000 1250"
                  aria-hidden
                >
                  <path d={region.path} />
                </svg>
              )}
              {region.links.map(
                ({ label, chinese, href, bounds: [x, y, width, height] }) => {
                  const name =
                    locale === "zh-CN" && chinese
                      ? `${label} · ${chinese}`
                      : label;
                  return (
                    <Link
                      key={label}
                      href={localeUrl(href, locale)}
                      aria-label={name}
                      title={name}
                      className={styles.hotspot}
                      style={{
                        left: `${x / 20}%`,
                        top: `${y / 12.5}%`,
                        width: `${width / 20}%`,
                        height: `${height / 12.5}%`,
                      }}
                    />
                  );
                },
              )}
            </div>
          ))}
        </div>
      </div>
    </figure>
  );
}
