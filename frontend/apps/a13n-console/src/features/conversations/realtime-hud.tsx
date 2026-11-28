import {
  CaretDownIcon,
  MicrophoneIcon,
  MicrophoneSlashIcon,
  PhoneDisconnectIcon,
  SpeakerSlashIcon,
} from "@phosphor-icons/react";
import {
  Badge,
  Button,
  Menu,
  MenuPopup,
  MenuRadioGroup,
  MenuRadioItem,
  MenuTrigger,
  StatusPill,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
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
  const active = phase === "ready";
  const warning = active && remaining !== undefined;
  const state =
    phase === "connecting"
      ? t("Connecting…")
      : phase === "ending"
        ? t("Saving…")
        : warning
          ? t("Ending soon")
          : t("Live");
  const microphone = muted ? t("Unmute microphone") : t("Mute microphone");
  const devicePicker = t("Microphone device");
  const interruptLabel = t("Interrupt response");
  const hangUp = t("End call");
  return (
    <section className={styles.hud} aria-label={t("Voice call controls")}>
      <div className={styles.summary}>
        <StatusPill
          role="status"
          variant={warning ? "warning" : active ? "success" : "neutral"}
        >
          {state}
        </StatusPill>
        <span className={styles.elapsed}>
          {elapsed} · {transport === "webrtc" ? t("WebRTC") : t("PCM")}
        </span>
        {warning && (
          <span className={styles.elapsed} aria-label={t("Time remaining")}>
            {remaining}
          </span>
        )}
      </div>
      <div className={styles.controls}>
        <div className={styles.levels} data-muted={muted}>
          <AudioLevel
            label={t("Microphone level")}
            level={active && !muted ? input : 0}
          />
          <AudioLevel label={t("Speaker level")} level={active ? output : 0} />
        </div>
        <div className={styles.microphone}>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  type="button"
                  size="icon-sm"
                  variant={muted ? "secondary" : "ghost"}
                  disabled={!active}
                  aria-label={microphone}
                  onClick={() => media.current?.toggleMute()}
                />
              }
            >
              {muted ? (
                <MicrophoneSlashIcon size={16} aria-hidden="true" />
              ) : (
                <MicrophoneIcon size={16} aria-hidden="true" />
              )}
            </TooltipTrigger>
            <TooltipPopup>{microphone}</TooltipPopup>
          </Tooltip>
          {devices.length > 1 && (
            <Menu>
              <Tooltip>
                <TooltipTrigger
                  render={
                    <MenuTrigger
                      render={
                        <Button
                          type="button"
                          size="icon-xs"
                          variant="ghost"
                          aria-label={devicePicker}
                          disabled={!active || switching}
                        />
                      }
                    />
                  }
                >
                  <CaretDownIcon size={12} aria-hidden="true" />
                </TooltipTrigger>
                <TooltipPopup>{devicePicker}</TooltipPopup>
              </Tooltip>
              <MenuPopup align="end" aria-label={devicePicker}>
                <MenuRadioGroup
                  value={deviceId}
                  onValueChange={(id) => {
                    void media.current?.switchMic(id);
                  }}
                >
                  {devices.map((device, index) => (
                    <MenuRadioItem
                      key={device.deviceId}
                      value={device.deviceId}
                    >
                      {device.label ||
                        t("Microphone {{number}}", { number: index + 1 })}
                    </MenuRadioItem>
                  ))}
                </MenuRadioGroup>
              </MenuPopup>
            </Menu>
          )}
        </div>
        {active && interrupt && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  aria-label={interruptLabel}
                  onClick={interrupt}
                />
              }
            >
              <SpeakerSlashIcon size={16} aria-hidden="true" />
            </TooltipTrigger>
            <TooltipPopup>{interruptLabel}</TooltipPopup>
          </Tooltip>
        )}
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                type="button"
                size="icon-sm"
                variant="destructive-outline"
                disabled={phase === "ending"}
                aria-label={hangUp}
                onClick={stop}
              />
            }
          >
            <PhoneDisconnectIcon size={16} aria-hidden="true" />
          </TooltipTrigger>
          <TooltipPopup>{hangUp}</TooltipPopup>
        </Tooltip>
      </div>
    </section>
  );
}

function AudioLevel({ label, level }: { label: string; level: number }) {
  const value = Math.min(1, level * 3);
  return (
    <span
      className={styles.track}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={value}
    >
      <span
        className={styles.level}
        style={{ transform: `scaleX(${value})` }}
      />
    </span>
  );
}
