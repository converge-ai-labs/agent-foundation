import { NavLink, useNavigate } from "react-router";
import { Button, Menu, MenuTrigger, MenuPopup, MenuItem } from "a13n-ui";
import {
  ChatCircle,
  DotsThree,
  PencilSimple,
  ShareNetwork,
  SlidersHorizontal,
} from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import {
  ParticipantAvatars,
  threadParticipants,
} from "../shell/participant-avatars";
import styles from "./conversation.module.css";

export function ThreadRow({
  row,
  presence,
}: {
  row: Pick<Schema<"ThreadActivityView">, "thread"> &
    Partial<Schema<"ThreadActivityView">>;
  presence: Schema<"PresenceFrame"> | null;
}) {
  const navigate = useNavigate();
  return (
    <div key={row.thread.thread_id} className={styles.threadRow}>
      <NavLink
        to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
        className={({ isActive }) =>
          `${styles.threadLink} ${isActive ? styles.selected : ""}`
        }
      >
        <ChatCircle />
        <span>
          <strong>
            {row.thread.title ||
              row.thread.excerpt?.first_input ||
              "Untitled conversation"}
          </strong>
          <small>
            {row.pending_decision
              ? "Needs your answer"
              : row.thread.root_activity.state !== "inactive"
                ? row.thread.root_activity.state
                : row.latest_operation?.status === "failed"
                  ? "Failed"
                  : row.thread.archived
                    ? "Archived"
                    : ""}
          </small>
        </span>
      </NavLink>
      <ParticipantAvatars
        participants={threadParticipants(presence, row.thread.thread_id)}
        ownId={presence?.participant_id}
        threadTitle={
          row.thread.title ||
          row.thread.excerpt?.first_input ||
          "Untitled conversation"
        }
      />
      <Menu>
        <MenuTrigger
          render={<Button variant="ghost" size="icon-sm" />}
          className={styles.threadActions}
          aria-label={`Actions for ${row.thread.title || "Untitled conversation"}`}
        >
          <DotsThree />
        </MenuTrigger>
        <MenuPopup align="start" side="right">
          {(
            [
              ["rename", "Rename conversation", PencilSimple],
              ["share", "Share conversation", ShareNetwork],
              ["comments", "Comments", ChatCircle],
              ["details", "Conversation details", SlidersHorizontal],
            ] as const
          ).map(([action, label, Icon]) => (
            <MenuItem
              key={action}
              onClick={() =>
                navigate(
                  `/threads/${encodeURIComponent(row.thread.thread_id)}?dialog=${action}`,
                )
              }
            >
              <Icon />
              {label}
            </MenuItem>
          ))}
        </MenuPopup>
      </Menu>
    </div>
  );
}
