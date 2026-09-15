import {
  Button,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
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

function Avatar({ participant }: { participant: Participant }) {
  const name = participant.display_name?.trim() || "Anonymous";
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
  if (!participants.length) return null;
  const count = participants.length;
  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={150}
        render={<Button variant="ghost" size="sm" />}
        className={styles.trigger}
        aria-label={`${count} ${count === 1 ? "person" : "people"} online in ${threadTitle}`}
      >
        <span className={styles.avatars}>
          {participants.slice(0, 3).map((participant) => (
            <Avatar
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
          In this conversation <span>{count}</span>
        </PopoverTitle>
        <ul className={styles.people}>
          {participants.map((participant) => (
            <li key={participant.participant_id}>
              <Avatar participant={participant} />
              <div className={styles.identity}>
                <span className={styles.name}>
                  {participant.display_name?.trim() || "Anonymous"}
                  {participant.participant_id === ownId && (
                    <span className={styles.you}> (you)</span>
                  )}
                </span>
                <small>
                  <span
                    className={styles.statusDot}
                    data-away={!participant.foreground || undefined}
                  />
                  {participant.foreground ? "Active" : "Away"} ·{" "}
                  {participant.focus?.target.kind === "conversation"
                    ? "Chat"
                    : participant.focus?.target.kind === "changes"
                      ? "Changes"
                      : participant.focus?.target.kind === "terminal"
                        ? "Terminal"
                        : "Files"}
                </small>
              </div>
            </li>
          ))}
        </ul>
      </PopoverPopup>
    </Popover>
  );
}
