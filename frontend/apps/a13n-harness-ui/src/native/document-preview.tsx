import { useEffect, useState, useRef } from "react";
import { Button } from "a13n-ui";
import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { fileTransfer } from "./file-transfer";
import { documentLimit, type DocumentKind } from "./document-kind";
import styles from "./document-preview.module.css";

type Pages = {
  count: number;
  draw: (index: number, canvas: HTMLCanvasElement) => Promise<void>;
  dispose: () => void;
};
type Sheets = { names: string[]; rows: (index: number) => string[][] };

// Inspect only ordinary, bounded Office ZIPs before asking a renderer to inflate them.
function checkOfficeZip(bytes: ArrayBuffer) {
  const data = new DataView(bytes);
  const fail = () => {
    throw Error("Invalid or oversized Office archive");
  };
  let end = bytes.byteLength - 22;
  while (
    end >= Math.max(0, bytes.byteLength - 65557) &&
    data.getUint32(end, true) !== 0x06054b50
  )
    end--;
  if (end < Math.max(0, bytes.byteLength - 65557)) return fail();
  const count = data.getUint16(end + 10, true);
  if (count > 4096 || !count) return fail();
  let pos = data.getUint32(end + 16, true),
    total = 0;
  for (let i = 0; i < count; i++) {
    if (pos + 46 > end || data.getUint32(pos, true) !== 0x02014b50)
      return fail();
    total += data.getUint32(pos + 24, true);
    if (total > 64 * 1024 * 1024) return fail();
    pos +=
      46 +
      data.getUint16(pos + 28, true) +
      data.getUint16(pos + 30, true) +
      data.getUint16(pos + 32, true);
  }
}
async function loadDocument(
  kind: DocumentKind,
  bytes: ArrayBuffer,
): Promise<Pages | Sheets> {
  if (bytes.byteLength > documentLimit) throw Error("Document too large");
  if (kind === "pdf") {
    const [pdfjs, worker] = await Promise.all([
      import("pdfjs-dist"),
      import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
    ]);
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
    const resources = new URL(
      import.meta.env.BASE_URL + `assets/pdfjs-${pdfjs.version}/`,
      window.location.origin,
    ).href;
    const loading = pdfjs.getDocument({
      data: bytes,
      cMapUrl: resources + "cmaps/",
      standardFontDataUrl: resources + "standard_fonts/",
      wasmUrl: resources + "wasm/",
      iccUrl: resources + "iccs/",
      useSystemFonts: true,
      disableFontFace: true,
      useWasm: false,
      maxImageSize: 16_000_000,
    });
    const pdf = await loading.promise.catch(async (err) => {
      await loading.destroy();
      throw err;
    });
    return {
      count: pdf.numPages,
      dispose: () => {
        void loading.destroy();
      },
      async draw(i, canvas) {
        const page = await pdf.getPage(i + 1),
          size = page.getViewport({ scale: 1 });
        const vp = page.getViewport({
          scale: Math.min(1440 / size.width, 2048 / size.height, 2),
        });
        canvas.width = Math.ceil(vp.width);
        canvas.height = Math.ceil(vp.height);
        await page.render({ canvas, viewport: vp }).promise;
      },
    };
  }
  const signature = new DataView(bytes).getUint32(0);
  if (signature === 0x504b0304) checkOfficeZip(bytes);
  else if (kind === "slides" || signature !== 0xd0cf11e0)
    throw Error("Unrecognized Office document");
  if (kind === "slides") {
    const { default: Renderer } = await import("pptx-browser");
    const deck = new Renderer();
    try {
      await deck.load(bytes);
      if (!deck.slideCount || !(deck.slideSize.cx > 0 && deck.slideSize.cy > 0))
        throw Error("Invalid slide size");
      return {
        count: deck.slideCount,
        dispose: () => deck.destroy(),
        draw: (i, canvas) =>
          deck.renderSlide(
            i,
            canvas,
            Math.max(
              1,
              Math.min(
                1440,
                Math.floor((2048 * deck.slideSize.cx) / deck.slideSize.cy),
              ),
            ),
          ),
      };
    } catch (err) {
      deck.destroy();
      throw err;
    }
  }
  const x = await import("xlsx");
  const book = x.read(bytes, {
    type: "array",
    sheetRows: 201,
    cellHTML: false,
    bookVBA: false,
  });
  const names = book.SheetNames.filter(
    (_, i) => !book.Workbook?.Sheets?.[i]?.Hidden,
  );
  if (!names.length) throw Error("Empty workbook");
  return {
    names,
    rows(i) {
      const sheet = book.Sheets[names[i]!]!;
      if (!sheet["!ref"]) return [];
      const range = x.utils.decode_range(sheet["!ref"]);
      range.s = { r: 0, c: 0 };
      range.e.r = Math.min(range.e.r, 199);
      range.e.c = Math.min(range.e.c, 39);
      return x.utils.sheet_to_json<string[]>(sheet, {
        header: 1,
        range,
        raw: false,
        defval: "",
        blankrows: true,
      });
    },
  };
}
export function DocumentPreview({
  kind,
  path,
  revision,
}: {
  kind: DocumentKind;
  path: string;
  revision: string;
}) {
  const transport = useTransport(),
    { t } = useTranslation();
  const [model, setModel] = useState<Pages | Sheets>(),
    [index, setIndex] = useState(0),
    [failed, setFailed] = useState(false);
  const [pageReady, setPageReady] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    let loaded: Pages | Sheets | undefined;
    (async () => {
      const access = await fileTransfer(
        transport,
        path,
        revision,
        "attachment",
        controller.signal,
      );
      const response = await transport.fetch(access.url, {
        signal: controller.signal,
      });
      loaded = await loadDocument(kind, await response.arrayBuffer());
      if (controller.signal.aborted) {
        if ("dispose" in loaded) loaded.dispose();
        return;
      }
      setModel(loaded);
    })().catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => {
      controller.abort();
      if (loaded && "dispose" in loaded) loaded.dispose();
    };
  }, [transport, path, revision, kind]);
  useEffect(() => {
    if (!model || !("draw" in model) || !host.current) return;
    const target = host.current,
      canvas = document.createElement("canvas");
    let current = true;
    model.draw(index, canvas).then(
      () => {
        if (current) {
          target.replaceChildren(canvas);
          setPageReady(true);
        }
      },
      () => {
        if (current) setFailed(true);
      },
    );
    return () => {
      current = false;
    };
  }, [model, index]);
  if (failed)
    return (
      <p role="status">
        {t(
          "This document could not be previewed. Download the original to open it.",
        )}
      </p>
    );
  if (!model) return <span hidden data-file-pending="" />;
  const sheet = "names" in model,
    count = sheet ? model.names.length : model.count;
  return (
    <section className={styles.preview} aria-label={t("Document preview")}>
      <div className={styles.navigation}>
        {sheet ? (
          <select
            aria-label={t("Worksheet")}
            value={index}
            onChange={(e) => setIndex(Number(e.target.value))}
          >
            {model.names.map((name, i) => (
              <option key={name} value={i}>
                {name}
              </option>
            ))}
          </select>
        ) : (
          <span>
            {index + 1} / {count}
          </span>
        )}
        {!sheet && count > 1 && (
          <>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("Previous page")}
              onClick={() => setIndex(index - 1)}
              disabled={!index}
            >
              <CaretLeft />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("Next page")}
              onClick={() => setIndex(index + 1)}
              disabled={index === count - 1}
            >
              <CaretRight />
            </Button>
          </>
        )}
      </div>
      {sheet ? (
        <>
          <div className={styles.grid}>
            <table>
              <tbody>
                {model.rows(index).map((row, r) => (
                  <tr key={r}>
                    <th scope="row">{r + 1}</th>
                    {row.map((cell, c) => (
                      <td key={c}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <small>{t("Preview shows up to 200 rows and 40 columns.")}</small>
        </>
      ) : (
        <div
          className={styles.page}
          ref={host}
          data-file-pending={pageReady ? undefined : ""}
        />
      )}
    </section>
  );
}
