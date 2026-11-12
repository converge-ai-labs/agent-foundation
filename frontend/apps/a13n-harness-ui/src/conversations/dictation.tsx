import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { Microphone, Stop, X, CircleNotch } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import {
  dictationSnapshot,
  type DictationDraft,
  type DictationTarget,
} from "./dictation-context";
import type { ReplyQuote } from "./reply-quote";
import { recordAudio, encodeAudio } from "./dictation-audio";
import styles from "./dictation.module.css";

// Six CSS pixels per 64 ms of captured audio.
const pixelsPerMs = 6 / 64;

export function Dictation({
  target,
  quote,
  disabled,
  onActive,
  readDraft,
  insert,
}: {
  target: DictationTarget;
  quote?: ReplyQuote;
  disabled?: boolean;
  onActive: (active: boolean) => void;
  readDraft: () => DictationDraft | undefined;
  insert: (text: string) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [phase, setPhase] = useState<
    "idle" | "requesting" | "recording" | "transcribing" | "failed"
  >("idle");
  const [levels, setLevels] = useState<{ id: number; value: number }[]>([]);
  const rail = useRef<HTMLDivElement>(null);
  const firstSampleAt = useRef<number | undefined>(undefined);
  // Move the whole strip at display cadence. Audio callbacks only append bars.
  useLayoutEffect(() => {
    const strip = rail.current;
    if (phase !== "recording" || !strip) return;
    let frame: number;
    const draw = () => {
      if (firstSampleAt.current !== undefined) {
        const x = (performance.now() - firstSampleAt.current) * pixelsPerMs;
        strip.style.transform = `translate3d(${-x}px, 0, 0)`;
      }
      frame = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, [phase]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  const finish = useRef<() => Promise<void>>(undefined);
  const pending = useRef<Schema<"DictationInput">>(undefined);
  const sending = useRef(false);
  useEffect(
    () => () => {
      if (controller.current) {
        controller.current.abort();
        onActive(false);
      }
    },
    [onActive],
  );
  async function transcribe() {
    const ctl = controller.current,
      body = pending.current;
    if (!ctl || !body || sending.current) return;
    sending.current = true;
    setPhase("transcribing");
    setError("");
    try {
      const response = await result(
        client.POST("/api/dictation", { body, signal: ctl.signal }),
      );
      if (ctl.signal.aborted) return;
      if (!response.text.trim()) throw new Error(t("No speech detected."));
      reset();
      insert(response.text);
      if (response.warning)
        setNotice(t("Cleanup unavailable; using the original transcript."));
    } catch (e) {
      if (!ctl.signal.aborted) {
        setError(e instanceof Error ? e.message : t("Voice input failed."));
        setPhase("failed");
      }
    } finally {
      if (controller.current === ctl) sending.current = false;
    }
  }
  function reset() {
    controller.current?.abort();
    controller.current = null;
    pending.current = undefined;
    sending.current = false;
    setError("");
    setNotice("");
    finish.current = undefined;
    setPhase("idle");
    onActive(false);
  }
  async function start() {
    if (controller.current) return;
    const draft = readDraft();
    if (!draft) return;
    const ctl = new AbortController();
    controller.current = ctl;
    const fail = (e: unknown) => {
      if (ctl.signal.aborted) return;
      reset();
      setError(
        e instanceof Error && e.name === "NotAllowedError"
          ? t("Microphone permission denied.")
          : e instanceof Error
            ? t(e.message)
            : t("Voice input failed."),
      );
    };
    setError("");
    setNotice("");
    setLevels([]);
    firstSampleAt.current = undefined;
    onActive(true);
    setPhase("requesting");
    try {
      const context = dictationSnapshot(target, quote, draft);
      let sequence = 0;
      let smoothedLevel = 0;
      const stop = await recordAudio(
        ctl.signal,
        (value) => {
          firstSampleAt.current ??= performance.now();
          smoothedLevel += (value - smoothedLevel) * 0.6;
          const sample = { id: sequence++, value: smoothedLevel };
          setLevels((history) => [...history.slice(-511), sample]);
        },
        fail,
      );
      if (ctl.signal.aborted) return;
      setPhase("recording");
      finish.current = async () => {
        finish.current = undefined;
        setPhase("transcribing");
        try {
          const audio = await encodeAudio(await stop());
          ctl.signal.throwIfAborted();
          pending.current = {
            audio,
            title: target.title ?? "",
            thread_id: target.threadId,
            ...context,
          };
          await transcribe();
        } catch (e) {
          fail(e);
        }
      };
    } catch (e) {
      fail(e);
    }
  }
  if (phase === "idle")
    return (
      <>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          data-dictation-mic
          disabled={disabled}
          onMouseDown={(e) => e.preventDefault()}
          onClick={start}
          title={t("Voice input")}
          aria-label={t("Voice input")}
        >
          <Microphone />
        </Button>
        {error && (
          <span role="alert" className={styles.error}>
            {error}
          </span>
        )}
        {notice && (
          <span role="status" className={styles.failed}>
            {notice}
          </span>
        )}
      </>
    );
  return (
    <div className={styles.bar} data-dictation-active>
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        aria-label={t("Cancel voice input")}
        onClick={reset}
      >
        <X />
      </Button>
      {phase === "failed" ? (
        <span role="alert" className={styles.failed} title={error}>
          {t("Transcription failed.")}
        </span>
      ) : (
        <div className={styles.wave} aria-hidden="true">
          <div ref={rail} className={styles.samples}>
            {levels.map(({ id, value }) => (
              <i key={id} style={{ left: id * 6, height: 3 + 15 * value }} />
            ))}
          </div>
        </div>
      )}
      <span role="status" className="sr-only">
        {phase === "failed"
          ? ""
          : phase === "recording"
            ? t("Recording")
            : phase === "requesting"
              ? t("Opening microphone…")
              : t("Transcribing…")}
      </span>
      {phase === "failed" ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => void transcribe()}
        >
          {t("Retry")}
        </Button>
      ) : phase === "recording" ? (
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          aria-label={t("Stop recording")}
          onClick={() => void finish.current?.()}
        >
          <Stop weight="fill" />
        </Button>
      ) : (
        <CircleNotch aria-hidden className={styles.spin} />
      )}
    </div>
  );
}
