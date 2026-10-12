import Image from "next/image";
import Link from "next/link";
import architecture from "../../../../.github/assets/a13n-architecture.webp";
import { type Locale, localeUrl } from "@/lib/i18n";
import styles from "./architecture-overview.module.css";

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

const regions: Region[] = [
  {
    name: "repository",
    links: [
      {
        label: "GitHub repository",
        chinese: "GitHub 仓库",
        href: "https://github.com/converge-ai-labs/agent-foundation",
        bounds: [1500, 20, 420, 55],
      },
    ],
  },
  {
    name: "clients",
    path: "M725 268 V288 Q725 308 745 308 H1405 Q1425 308 1425 288 V268 M1150 268 V308 M1000 308 V510",
    links: [
      { label: "SDKs", href: "/a13n-service/sdks", bounds: [475, 75, 105, 50] },
      ...["Go", "TypeScript", "Rust", "Python"].map((label, i): Hotspot => ({
        label,
        href: "/a13n-service/sdks#choose-a-client",
        bounds: [465 + i * 137, 130, 110, 120],
      })),
      {
        label: "HTTP API",
        href: "/a13n-service/api-reference",
        bounds: [1085, 130, 130, 120],
      },
      {
        label: "Web console",
        chinese: "Web 控制台",
        href: "/a13n-service/use-platform",
        bounds: [1340, 130, 170, 120],
      },
    ],
  },
  {
    name: "environments",
    path: "M571 613 H625",
    links: [
      {
        label: "Environments",
        chinese: "执行环境",
        href: "/environments",
        bounds: [125, 390, 250, 60],
      },
      {
        label: "E2B",
        href: "/environments/providers#e2b-runtime",
        bounds: [110, 465, 100, 120],
      },
      {
        label: "Daytona",
        href: "/environments/providers#daytona",
        bounds: [245, 465, 110, 120],
      },
      {
        label: "Runloop",
        href: "/environments/providers#runloop",
        bounds: [380, 465, 110, 120],
      },
      {
        label: "More environments",
        chinese: "更多执行环境",
        href: "/environments/providers",
        bounds: [500, 475, 60, 60],
      },
      {
        label: "Local",
        chinese: "本地环境",
        href: "/environments/providers#local-envd-runtime",
        bounds: [110, 625, 100, 125],
      },
      {
        label: "Docker",
        href: "/environments/providers#docker-runtime",
        bounds: [245, 625, 110, 125],
      },
      {
        label: "Envd",
        href: "/environments/remote-envd",
        bounds: [380, 625, 110, 140],
      },
    ],
  },
  {
    name: "foundation",
    links: [
      {
        label: "Agent Foundation",
        href: "/overview/core-concepts",
        bounds: [640, 550, 340, 125],
      },
      {
        label: "Service",
        href: "/a13n-service",
        bounds: [1020, 566, 140, 50],
      },
      {
        label: "Harness",
        href: "/a13n-harness",
        bounds: [1190, 566, 155, 50],
      },
    ],
  },
  {
    name: "memory",
    path: "M1400 582 V524 Q1400 504 1420 504 H1476",
    links: [
      {
        label: "Memory",
        chinese: "记忆",
        href: "/a13n-service/memory",
        bounds: [1535, 390, 160, 60],
      },
      {
        label: "File memory",
        chinese: "文件记忆",
        href: "/a13n-service/memory#create-a-file-memory",
        bounds: [1505, 465, 120, 125],
      },
      {
        label: "mem0",
        href: "/a13n-service/memory#record-memories",
        bounds: [1665, 465, 120, 125],
      },
      {
        label: "More memory options",
        chinese: "更多记忆选项",
        href: "/a13n-service/memory",
        bounds: [1815, 475, 65, 60],
      },
    ],
  },
  {
    name: "ui",
    path: "M1400 646 V706 Q1400 726 1420 726 H1476",
    links: [
      {
        label: "Harness UI",
        href: "/a13n-harness-ui",
        bounds: [1535, 610, 180, 60],
      },
      {
        label: "WebUI",
        href: "/a13n-harness-ui/webui",
        bounds: [1505, 695, 120, 115],
      },
      {
        label: "Terminal UI",
        chinese: "终端界面",
        href: "/a13n-harness-ui/everyday-use",
        bounds: [1660, 695, 135, 115],
      },
    ],
  },
  {
    name: "models",
    path: "M1000 725 V866 H382 Q362 866 362 886 V901",
    links: [
      {
        label: "Models",
        chinese: "模型",
        href: "/a13n-service/models",
        bounds: [92, 936, 120, 48],
      },
      {
        label: "GPT",
        href: "/a13n-service/models",
        bounds: [90, 1010, 225, 65],
      },
      {
        label: "Claude",
        href: "/a13n-service/models",
        bounds: [330, 1010, 225, 65],
      },
      {
        label: "Gemini",
        href: "/a13n-service/models",
        bounds: [151, 1108, 100, 44],
      },
      {
        label: "DeepSeek",
        href: "/a13n-service/models",
        bounds: [330, 1095, 225, 65],
      },
      {
        label: "More models",
        chinese: "更多模型",
        href: "/a13n-service/models",
        bounds: [570, 1100, 65, 60],
      },
    ],
  },
  {
    name: "web",
    path: "M1000 725 V901",
    links: [
      {
        label: "Web data",
        chinese: "Web 数据",
        href: "/a13n-service/tools#web-search-and-scrape",
        bounds: [730, 930, 220, 60],
      },
      ...["Exa", "Tavily", "Firecrawl", "Jina"].map((label, i): Hotspot => ({
        label,
        href: "/a13n-service/tools#web-search-and-scrape",
        bounds: [730 + (i % 2) * 240, 1010 + Math.floor(i / 2) * 85, 225, 65],
      })),
      {
        label: "More web tools",
        chinese: "更多 Web 工具",
        href: "/a13n-service/tools#web-search-and-scrape",
        bounds: [1210, 1100, 65, 60],
      },
    ],
  },
  {
    name: "connections",
    path: "M1000 725 V866 H1618 Q1638 866 1638 886 V901",
    links: [
      {
        label: "Connections",
        chinese: "连接",
        href: "/a13n-service/tools#connections",
        bounds: [1370, 930, 250, 60],
      },
      ...["GitHub", "Notion", "Linear", "Figma"].map((label, i): Hotspot => ({
        label,
        href: "/a13n-service/tools#connections",
        bounds: [1370 + (i % 2) * 240, 1010 + Math.floor(i / 2) * 85, 225, 65],
      })),
      {
        label: "More connections",
        chinese: "更多连接",
        href: "/a13n-service/tools#connections",
        bounds: [1850, 1100, 65, 60],
      },
    ],
  },
];

export function ArchitectureOverview({
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
      <div
        className={styles.scroll}
        role="region"
        aria-label={alt}
        tabIndex={0}
      >
        <div className={styles.canvas}>
          <Image
            src={architecture}
            alt={alt}
            className={styles.artwork}
            preload
          />
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
      <figcaption className={styles.hint}>{hint}</figcaption>
    </figure>
  );
}
