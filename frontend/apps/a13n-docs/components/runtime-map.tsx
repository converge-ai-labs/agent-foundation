import Link from "next/link";
import type { ReactNode } from "react";
import { type Locale, localeUrl } from "@/lib/i18n";
import {
  ApiPort,
  CheckpointStore,
  Glyph,
  HarnessLoop,
  RequestWindow,
  RunStore,
  ServiceMark,
  TraceWindow,
} from "./architecture-artwork";
import s from "./runtime-map.module.css";

type Box = readonly [x: number, y: number, width: number, height: number];
type Point = readonly [x: number, y: number];
type PlateProps = { compact: boolean; locale: Locale };
const local = (locale: Locale, en: string, zh: string) =>
  locale === "en" ? en : zh;

function Target({
  href,
  locale,
  name,
  box,
  children,
}: {
  href: string;
  locale: Locale;
  name: string;
  box: Box;
  children: ReactNode;
}) {
  const [x, y, width, height] = box;
  return (
    <Link href={localeUrl(href, locale)} aria-label={name} className={s.link}>
      <title>{name}</title>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx="6"
        className={s.hit}
      />
      {children}
    </Link>
  );
}

const desktop = {
  frame: [204, 28, 606, 366],
  api: [295, 155],
  sql: [462, 155],
  harness: [689, 155],
  object: [689, 298],
  recovery: [227, 258, 337, 114],
  clients: [
    [29, 97],
    [29, 235],
  ],
} as const;
const mobile = {
  frame: [16, 143, 328, 375],
  api: [84, 234],
  sql: [248, 234],
  harness: [248, 360],
  object: [83, 354],
  recovery: [29, 413, 302, 95],
  clients: [
    [21, 17],
    [192, 17],
  ],
} as const;
const layout = (compact: boolean) => (compact ? mobile : desktop);

function Boundary({ compact, locale }: PlateProps) {
  const [x, y, w, h] = layout(compact).frame;
  const outline = `M${x} ${y}h${w - 13}l13 13v${h - 13}h-${w}z`;
  return (
    <>
      <path d={outline} className={s.boundary} />
      <path
        d={outline}
        fill="url(#architecture-grid)"
        mask="url(#grid-fade)"
        aria-hidden="true"
      />
      <Target
        href="/a13n-service"
        name="a13n Service"
        locale={locale}
        box={[x + 12, y + 8, compact ? 175 : 230, 39]}
      >
        <ServiceMark
          x={x + 18}
          y={y + (compact ? 14 : 15)}
          size={compact ? 19 : 27}
        />
        <text
          x={x + (compact ? 44 : 53)}
          y={y + (compact ? 31 : 39)}
          className={s.serviceTitle}
        >
          a13n Service
        </text>
      </Target>
      <text
        x={x + w - 19}
        y={y + (compact ? 29 : 33)}
        textAnchor="end"
        className={s.deployment}
      >
        {local(locale, "Self-hosted", "自行部署")}
      </text>
    </>
  );
}

function Clients({ compact, locale }: PlateProps) {
  const [consolePos, appPos] = layout(compact).clients;
  function client([x, y]: Point, isApp: boolean) {
    const name = isApp
      ? local(locale, "Your application", "你的应用")
      : "Console";
    return (
      <g transform={`translate(${x} ${y})`}>
        <Target
          locale={locale}
          href={
            isApp
              ? "/a13n-service/connect-application"
              : "/a13n-service/use-platform"
          }
          name={name}
          box={[-2, -2, 148, compact ? 101 : 110]}
        >
          {isApp ? (
            <RequestWindow width={139} height={57} />
          ) : (
            <TraceWindow width={139} height={63} />
          )}
          <text y={compact ? 81 : 85} className={s.clientTitle}>
            {name}
          </text>
          <text y={compact ? 98 : 104} className={s.note}>
            {isApp
              ? "SDK / HTTP API"
              : local(locale, "Configure · debug", "配置 · 调试")}
          </text>
        </Target>
      </g>
    );
  }
  return (
    <>
      {!compact && (
        <text x="29" y="66" className={s.sectionTitle}>
          {local(locale, "Clients", "客户端")}
        </text>
      )}
      {client(consolePos, false)}
      {client(appPos, true)}
    </>
  );
}

function Connections({ compact, locale }: PlateProps) {
  const marks = (t: string) => `url(#map-arrow-${t})`;
  const common = compact
    ? {
        ingress: "M88 121v12H14l-6 6v89l6 6h47",
        app: "M258 121v6l-6 6H88",
        stream: "M61 224H20l-6-6V62l6-6h1",
        persist: "M107 234h117",
        lease: "M271 234h39l10 10v106l-10 10h-6",
        state: "M190 360h-61l-6-6h-17",
        resource: "M248 389v5l6 6h91l7 7v133l-7 7h-1",
      }
    : {
        ingress: "M168 128h11l18 18v3l6 6h62",
        app: "M168 261h10l19-19v-93",
        stream: "M265 141h-53l-10-10v-9l-10-10h-24",
        persist: "M324 155h107",
        lease: "M492 155h102",
        state: "M689 195v73",
        resource:
          "M783 155h50 M833 155v-35l8-8h13 M833 155v139l8 8h13 M833 158h21 M833 206h21 M833 254h21",
      };
  const labels = compact
    ? [
        [165, 222],
        [312, 315],
        [150, 347],
      ]
    : [
        [375, 142],
        [545, 142],
        [698, 240],
      ];
  const labelText = [
    local(locale, "persist entry", "保存条目"),
    local(locale, "run lease", "运行租约"),
    local(locale, "save / restore", "保存 / 恢复"),
  ];
  return (
    <g aria-hidden="true">
      <g className={s.wires}>
        <g className={s.requestWire}>
          <path d={common.ingress} markerEnd={marks("request")} />
          <path d={common.app} />
          <path
            d={common.stream}
            className={s.stream}
            markerEnd={marks("request")}
          />
          <path d={common.persist} markerEnd={marks("request")} />
        </g>
        <path
          className={s.execWire}
          d={common.lease}
          markerEnd={marks("exec")}
        />
        <path
          className={s.stateWire}
          d={common.state}
          markerStart={marks("state")}
          markerEnd={marks("state")}
        />
        <path className={s.resourceWire} d={common.resource} />
      </g>
      {!compact && (
        <text x="188" y="107" className={s.streamLabel}>
          SSE
        </text>
      )}
      {labels.map(([x, y], i) => (
        <text
          key={i}
          x={x}
          y={y}
          className={s.wireLabel}
          style={{
            textAnchor:
              compact && i === 1
                ? "end"
                : !compact && i === 2
                  ? "start"
                  : "middle",
          }}
        >
          {labelText[i]}
        </text>
      ))}
    </g>
  );
}

function Runtime({ compact, locale }: PlateProps) {
  const { api, sql, harness, object } = layout(compact);
  const scale = compact ? 0.78 : 1;
  const halfWidth = compact ? 56 : 94,
    halfHeight = compact ? 29 : 38;
  const [hx, hy] = harness;
  return (
    <>
      <g transform={`translate(${api[0]} ${api[1]})`}>
        <Target
          locale={locale}
          href="/a13n-service/api-reference"
          name="HTTP API"
          box={[-45, -59, 90, 126]}
        >
          <text y={compact ? -34 : -45} className={s.role} textAnchor="middle">
            control
          </text>
          <g transform={`scale(${scale})`}>
            <ApiPort />
          </g>
          <text
            y={compact ? 49 : 59}
            textAnchor="middle"
            className={s.nodeName}
          >
            HTTP API
          </text>
        </Target>
      </g>
      <g transform={`translate(${sql[0]} ${sql[1]})`}>
        <Target
          locale={locale}
          href="/a13n-service/operations#backups"
          name="PostgreSQL"
          box={[-69, -35, 138, 116]}
        >
          <g transform={`scale(${scale})`}>
            <RunStore />
          </g>
          <text
            y={compact ? 49 : 59}
            textAnchor="middle"
            className={s.nodeName}
          >
            PostgreSQL
          </text>
          <text y={compact ? 65 : 78} textAnchor="middle" className={s.note}>
            {local(locale, "Threads · runs · leases", "线程 · 运行 · 租约")}
          </text>
        </Target>
      </g>
      <g transform={`translate(${hx} ${hy})`}>
        <path
          d={`M${-halfWidth} ${-halfHeight}h${halfWidth * 2 - 10}l10 10v${halfHeight * 2 - 10}h-${halfWidth * 2}Z`}
          className={s.processor}
        />
        <Target
          locale={locale}
          href="/a13n-service/operations#process-roles"
          name={local(locale, "Worker role", "Worker 角色")}
          box={[-halfWidth, -halfHeight, halfWidth * 2 - 10, compact ? 22 : 28]}
        >
          <text
            x={-halfWidth + 12}
            y={-halfHeight + (compact ? 15 : 20)}
            className={s.workerRole}
          >
            worker
          </text>
        </Target>
        <Target
          locale={locale}
          href="/a13n-harness"
          name="Harness"
          box={[
            -halfWidth,
            -halfHeight + (compact ? 22 : 28),
            halfWidth * 2,
            compact ? 36 : 48,
          ]}
        >
          <text x={-halfWidth + 12} y={compact ? 15 : 20} className={s.harness}>
            Harness
          </text>
          <g color="#f5f2ff">
            <HarnessLoop
              x={halfWidth - (compact ? 30 : 55)}
              y={compact ? -6 : -17}
              size={compact ? 22 : 44}
            />
          </g>
        </Target>
      </g>
      <g transform={`translate(${object[0]} ${object[1]})`}>
        <Target
          locale={locale}
          href="/a13n-service/operations#backups"
          name={local(locale, "Object storage", "对象存储")}
          box={[-72, -29, 144, compact ? 78 : 99]}
        >
          <g transform={`scale(${scale})`}>
            <CheckpointStore />
          </g>
          <text
            y={compact ? 35 : 47}
            textAnchor="middle"
            className={s.nodeName}
          >
            {local(locale, "Object storage", "对象存储")}
          </text>
          <text y={compact ? 49 : 65} textAnchor="middle" className={s.note}>
            {local(locale, "Run checkpoints", "运行检查点")}
          </text>
        </Target>
      </g>
    </>
  );
}

function Recovery({ compact, locale }: PlateProps) {
  const [x, y, w, h] = layout(compact).recovery;
  const start = compact ? 75 : 80,
    saved = compact ? 117 : 128,
    lost = compact ? 182 : 209,
    resume = compact ? 252 : 280;
  const rowA = compact ? 53 : 66,
    rowB = compact ? 76 : 90;
  const name = local(locale, "Run recovery", "运行恢复");
  return (
    <g transform={`translate(${x} ${y})`}>
      <path d={`M0 0h${w}`} className={s.divider} />
      <Target
        locale={locale}
        href="/a13n-service/operations#process-roles"
        name={name}
        box={[0, 0, w, h]}
      >
        <text x="14" y={compact ? 21 : 24} className={s.chartTitle}>
          {name}
        </text>
        <g className={s.chartNotes} textAnchor="middle">
          <text x={saved} y={rowA - 12}>
            {local(locale, "checkpoint", "检查点")}
          </text>
          <text x={lost} y={rowA - 12}>
            {local(locale, "lease expires", "租约过期")}
          </text>
          <text x={resume} y={rowA - 12}>
            {local(locale, "resume", "恢复")}
          </text>
        </g>
        <g className={s.attempt}>
          <text x="14" y={rowA + 4}>
            {local(locale, "Attempt 1", "尝试 1")}
          </text>
          <text x="14" y={rowB + 4}>
            {local(locale, "Attempt 2", "尝试 2")}
          </text>
        </g>
        <g
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path
            d={`M${start} ${rowA}H${w - 14} M${start} ${rowB}H${w - 14} M${resume} ${rowA - 6}V${rowB - 6}`}
            className={s.axis}
          />
          <path
            d={`M${start} ${rowA}H${lost - 5}`}
            className={s.firstAttempt}
          />
          <path
            d={`M${saved} ${rowA + 4}V${rowB - 6}q0 6 6 6H${resume}`}
            className={s.restore}
          />
          <path d={`M${resume} ${rowB}H${w - 14}`} className={s.nextAttempt} />
          <path
            d={`M${lost - 3} ${rowA - 3}l6 6m0-6-6 6`}
            className={s.failure}
          />
          <path d={`M${saved} ${rowA - 5}l5 5-5 5-5-5z`} className={s.saved} />
          <circle cx={resume} cy={rowB} r="4" className={s.resumed} />
        </g>
      </Target>
    </g>
  );
}

const resources = [
  ["model", "Models", "模型", "/a13n-harness/models"],
  [
    "tools",
    "Tools & MCP",
    "工具与 MCP",
    "/a13n-harness/tools-and-dependencies",
  ],
  ["environment", "Environments", "执行环境", "/environments"],
  ["skills", "Skills", "技能", "/a13n-harness/skills"],
  ["memory", "Memory", "记忆", "/a13n-harness/memory"],
] as const;
function Resources({ compact, locale }: PlateProps) {
  const ys = [112, 158, 206, 254, 302];
  return (
    <>
      {compact && (
        <rect
          x="16"
          y="529"
          width="328"
          height="130"
          className={s.resourceBounds}
        />
      )}
      <text
        x={compact ? 24 : 850}
        y={compact ? 547 : 66}
        className={s.sectionTitle}
      >
        {local(locale, "Agent resources", "Agent 资源")}
      </text>
      {resources.map(([kind, en, zh, href], i) => {
        const name = local(locale, en, zh);
        const x = compact ? 24 + (i % 2) * 172 : 853,
          y = compact ? 575 + Math.floor(i / 2) * 34 : ys[i],
          r = compact ? 12 : 15;
        return (
          <g key={kind} transform={`translate(${x} ${y})`}>
            <Target
              href={href}
              locale={locale}
              name={name}
              box={[-3, -r - 3, compact ? 145 : 153, 2 * r + 6]}
            >
              {!compact && (
                <rect
                  x="-1.5"
                  y="-2.5"
                  width="4"
                  height="5"
                  fill="var(--resource-wire)"
                />
              )}
              <g className={s.resourceIcon}>
                <Glyph
                  kind={kind}
                  x={compact ? 0 : 9}
                  y={-r * 0.64}
                  size={r * 1.28}
                />
              </g>
              <text x={compact ? 22 : 38} y="5" className={s.resourceName}>
                {name}
              </text>
            </Target>
          </g>
        );
      })}
    </>
  );
}

function Hosts({ compact, locale }: PlateProps) {
  const entries = [
    ["/a13n-harness-ui", "Harness UI", "Terminal / WebUI", "environment"],
    [
      "/a13n-harness/getting-started",
      local(locale, "Python app", "Python 应用"),
      "import a13n_harness",
      "code",
    ],
  ] as const;
  return (
    <>
      <path d={compact ? "M18 671h324" : "M28 419h968"} className={s.divider} />
      <text
        x={compact ? 20 : 29}
        y={compact ? 697 : 465}
        className={s.sectionTitle}
      >
        {local(locale, "In-process hosts", "进程内运行")}
      </text>
      {entries.map(([href, name, detail, icon], i) => {
        const x = compact ? 20 + 165 * i : 205 + 395 * i,
          y = compact ? 711 : 435,
          w = compact ? 154 : 375,
          h = compact ? 86 : 58;
        return (
          <g key={href} transform={`translate(${x} ${y})`}>
            <path d={`M0 4v${h - 8}`} className={s.divider} />
            <Target href={href} locale={locale} name={name} box={[0, 0, w, h]}>
              <Glyph kind={icon} x={11} y={12} size={18} />
              <text x="36" y="27" className={s.hostTitle}>
                {name}
              </text>
              <text
                x={compact ? 12 : 36}
                y="45"
                className={icon === "code" ? s.code : s.note}
              >
                {detail}
              </text>
              <g
                transform={
                  compact ? "translate(11 55)" : `translate(${w - 90} 15)`
                }
              >
                <g className={s.embedMark}>
                  <HarnessLoop x={0} y={0} size={22} />
                </g>
                <text x="26" y="15.5" className={s.embedded}>
                  Harness
                </text>
              </g>
            </Target>
          </g>
        );
      })}
    </>
  );
}

function Map({ compact, locale }: PlateProps) {
  return (
    <svg
      width="100%"
      height="100%"
      viewBox={compact ? "0 0 360 816" : "0 0 1024 521"}
      className={compact ? s.compact : s.wide}
    >
      <Boundary compact={compact} locale={locale} />
      <Connections compact={compact} locale={locale} />
      <Clients compact={compact} locale={locale} />
      <Runtime compact={compact} locale={locale} />
      <Recovery compact={compact} locale={locale} />
      <Resources compact={compact} locale={locale} />
      <Hosts compact={compact} locale={locale} />
    </svg>
  );
}

export function RuntimeMap({
  alt,
  locale = "en",
}: {
  alt: string;
  locale?: Locale;
}) {
  return (
    <figure id="architecture" className={s.figure}>
      <figcaption className="sr-only">{alt}</figcaption>
      <svg
        role="group"
        className={s.plate}
        aria-label={local(
          locale,
          "Agent Foundation architecture",
          "Agent Foundation 架构",
        )}
      >
        <defs>
          <radialGradient id="grid-density">
            <stop offset="0" stopColor="white" />
            <stop offset=".65" stopColor="white" stopOpacity=".64" />
            <stop offset="1" stopColor="white" stopOpacity="0" />
          </radialGradient>
          <mask id="grid-fade" maskContentUnits="objectBoundingBox">
            <rect width="1" height="1" fill="url(#grid-density)" />
          </mask>
          <pattern
            id="architecture-grid"
            width="10"
            height="10"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r=".8" fill="var(--dots)" />
          </pattern>
          {(["request", "exec", "state"] as const).map((t) => (
            <marker
              key={t}
              id={`map-arrow-${t}`}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="2.25"
              markerHeight="2.25"
              orient="auto-start-reverse"
            >
              <path d="M0 0 10 5 0 10 2 5Z" fill={`var(--${t}-wire)`} />
            </marker>
          ))}
        </defs>
        <Map compact={false} locale={locale} />
        <Map compact locale={locale} />
      </svg>
    </figure>
  );
}
