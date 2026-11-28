import {
  MicrophoneIcon,
  MicrophoneSlashIcon,
  PhoneDisconnectIcon,
  SpeakerHighIcon,
  SpeakerSlashIcon,
} from "@phosphor-icons/react";
import { Badge, Button, Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import { useEffect, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import styles from "./realtime.module.css";

export interface VoiceMedia {
  levels: () => [input: number, output: number];
  toggleMute: () => void;
  switchMic: (id: string) => Promise<void>;
}

export function VoiceAlpha() {
  const { t } = useTranslation();
  return (
    <Tooltip>
      <TooltipTrigger render={<span tabIndex={0} className={styles.alpha} />}>
        <Badge variant="outline">{t("Alpha")}</Badge>
      </TooltipTrigger>
      <TooltipPopup>{t("The native Live API is in alpha.")}</TooltipPopup>
    </Tooltip>
  );
}

function clock(seconds: number) {
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}

export function VoiceHud({
  phase,
  transport,
  startedAt,
  limitAt,
  muted,
  devices,
  deviceId,
  switching,
  media,
  stop,
  interrupt,
}: {
  phase: "connecting" | "ready" | "ending";
  transport: "websocket" | "webrtc";
  startedAt: number;
  limitAt?: number;
  muted: boolean;
  devices: MediaDeviceInfo[];
  deviceId: string;
  switching: boolean;
  media: RefObject<VoiceMedia | null>;
  stop: () => void;
  interrupt?: () => void;
}) {
  const { t } = useTranslation();
  const [now, setNow] = useState(Date.now);
  const [[input, output], setLevels] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (phase !== "ready") return;
    const timer = window.setInterval(
      () => setLevels(media.current?.levels() ?? [0, 0]),
      67,
    );
    return () => window.clearInterval(timer);
  }, [media, phase]);
  const elapsed = clock(Math.max(0, Math.floor((now - startedAt) / 1000)));
  const remaining =
    limitAt === undefined
      ? undefined
      : clock(
          Math.max(0, Math.ceil((limitAt - Math.max(now, Date.now())) / 1000)),
        );
  const state =
    phase === "connecting"
      ? t("Connecting…")
      : phase === "ending"
        ? t("Saving conversation…")
        : t("Voice call active");
  return (
    <section className={styles.hud} aria-label={t("Voice call controls")}>
      <div className={styles.summary}>
        <span className={styles.state} role="status">
          {state}
        </span>
        <span className={styles.transport}>
          {transport === "webrtc" ? t("WebRTC") : t("PCM")}
        </span>
        <span className={styles.elapsed} aria-label={t("Elapsed call time")}>
          {elapsed}
        </span>
        <VoiceAlpha />
      </div>
      <div className={styles.controls}>
        <div className={styles.levels}>
          <MicrophoneIcon size={13} aria-hidden="true" />
          <meter
            max={1}
            value={phase === "ready" && !muted ? Math.min(1, input * 3) : 0}
            aria-label={t("Microphone level")}
          />
          <SpeakerHighIcon size={13} aria-hidden="true" />
          <meter
            max={1}
            value={phase === "ready" ? Math.min(1, output * 3) : 0}
            aria-label={t("Speaker level")}
          />
        </div>
        <Button
          type="button"
          size="icon-sm"
          variant={muted ? "secondary" : "ghost"}
          disabled={phase !== "ready"}
          aria-label={muted ? t("Unmute microphone") : t("Mute microphone")}
          title={muted ? t("Unmute microphone") : t("Mute microphone")}
          onClick={() => media.current?.toggleMute()}
        >
          {muted ? (
            <MicrophoneSlashIcon size={16} />
          ) : (
            <MicrophoneIcon size={16} />
          )}
        </Button>
        <select
          className={styles.device}
          aria-label={t("Microphone device")}
          disabled={phase !== "ready" || switching || !devices.length}
          value={deviceId}
          onChange={(event) =>
            void media.current?.switchMic(event.currentTarget.value)
          }
        >
          {!devices.some((device) => device.deviceId === deviceId) && (
            <option value={deviceId}>{t("Default microphone")}</option>
          )}
          {devices.map((device, index) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label ||
                t("Microphone {{number}}", { number: index + 1 })}
            </option>
          ))}
        </select>
        {phase === "ready" && interrupt && (
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label={t("Interrupt response")}
            title={t("Interrupt response")}
            onClick={interrupt}
          >
            <SpeakerSlashIcon size={16} aria-hidden="true" />
          </Button>
        )}
        <Button
          type="button"
          size="icon-sm"
          variant="outline"
          disabled={phase === "ending"}
          aria-label={t("End realtime chat")}
          title={t("End realtime chat")}
          onClick={stop}
        >
          <PhoneDisconnectIcon size={16} aria-hidden="true" />
        </Button>
      </div>
      {remaining !== undefined && (
        <div className={styles.warning}>
          <span role="status">{t("Call time limit approaching")}</span>
          <span className={styles.elapsed} aria-label={t("Time remaining")}>
            {remaining}
          </span>
        </div>
      )}
    </section>
  );
}
