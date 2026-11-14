import { useTranslation } from "../i18n";
import {
  Button,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
  ScrollArea,
} from "a13n-ui";
import type { CSSProperties } from "react";
import type { Schema } from "../transport/client";
import styles from "./participant-avatars.module.css";

type Participant = Schema<"ParticipantPresence">;

export function threadParticipants(
  presence: Schema<"PresenceFrame"> | null,
  threadId: string,
): Participant[] {
  if (presence?.closed) return [];
  return (presence?.participants ?? [])
    .filter((participant) => {
      const focus = participant.focus;
      return (
        participant.availability === "available" &&
        focus &&
        (focus.root_thread_id === threadId ||
          (focus.target.kind === "conversation" &&
            focus.target.thread_id === threadId))
      );
    })
    .sort(
      (a, b) =>
        Number(!!b.foreground) - Number(!!a.foreground) ||
        a.participant_id.localeCompare(b.participant_id),
    );
}

export function ParticipantAvatar({
  participant,
}: {
  participant: Participant;
}) {
  const { t } = useTranslation();
  const name = participant.display_name?.trim() || t("Anonymous");
  const words = name.split(/\s+/u);
  const initials = (
    Array.from(words[0])[0] +
    (words.length > 1 ? Array.from(words.at(-1)!)[0] : "")
  ).toUpperCase();
  return (
    <span
      className={styles.avatar}
      style={
        {
          "--participant-color": participant.color ?? "#64748b",
        } as CSSProperties
      }
      aria-hidden="true"
      data-away={!participant.foreground || undefined}
    >
      {initials}
    </span>
  );
}

export function ParticipantAvatars({
  participants,
  ownId,
  threadTitle,
}: {
  participants: Participant[];
  ownId?: string | null;
  threadTitle: string;
}) {
  const { t } = useTranslation();
  const people = [
    ...new Map(
      participants.map((participant) => [
        participant.participant_id,
        participant,
      ]),
    ).values(),
  ];
  if (!people.length) return null;
  const count = people.length;
  const title = threadTitle.trim();
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        render={<Button variant="ghost" size="sm" />}
        className={styles.trigger}
        aria-label={
          title
            ? t(
                count === 1
                  ? "{{count}} person online in {{title}}"
                  : "{{count}} people online in {{title}}",
                { count, title },
              )
            : `${t("People here")} (${count})`
        }
      >
        <span className={styles.avatars}>
          {people.slice(0, 3).map((participant) => (
            <ParticipantAvatar
              key={participant.participant_id}
              participant={participant}
            />
          ))}
          {count > 3 && (
            <span
              className={`${styles.avatar} ${styles.overflow}`}
              aria-hidden="true"
            >
              +{count - 3}
            </span>
          )}
        </span>
      </PopoverTrigger>
      <PopoverPopup side="right" align="start" className={styles.popup}>
        <PopoverTitle className={styles.title}>
          {t("People here")} <span>{count}</span>
        </PopoverTitle>
        <ScrollArea autoHeight scrollbarGutter className={styles.peopleScroll}>
          <ul className={styles.people}>
            {people.map((participant) => (
              <li key={participant.participant_id}>
                <ParticipantAvatar participant={participant} />
                <div className={styles.identity}>
                  <span className={styles.name}>
                    {participant.display_name?.trim() || t("Anonymous")}
                    {participant.participant_id === ownId && (
                      <span className={styles.you}> {t("(you)")}</span>
                    )}
                  </span>
                  <small>
                    <span
                      className={styles.statusDot}
                      data-away={!participant.foreground || undefined}
                    />
                    {t(participant.foreground ? "Active" : "Away")} -{" "}
                    {t(
                      participant.focus?.target.kind === "conversation"
                        ? "Chat"
                        : participant.focus?.target.kind === "changes"
                          ? "Changes"
                          : participant.focus?.target.kind === "terminal"
                            ? "Terminal"
                            : participant.focus?.target.kind === "file"
                              ? "Files"
                              : participant.focus?.target.kind === "project"
                                ? "Project"
                                : "Workspace",
                    )}
                  </small>
                </div>
              </li>
            ))}
          </ul>
        </ScrollArea>
      </PopoverPopup>
    </Popover>
  );
}
