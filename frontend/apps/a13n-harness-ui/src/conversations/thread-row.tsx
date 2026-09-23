import { useContext } from "react";
import { NavLink, useNavigate, useLocation } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Menu, MenuTrigger, MenuPopup, MenuItem } from "a13n-ui";
import {
  ChatCircle,
  Question,
  CircleNotch,
  WarningCircleIcon,
  Archive,
  ArrowCounterClockwise,
  DotsThree,
  PencilSimple,
  ShareNetwork,
  SlidersHorizontal,
} from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { refreshThreadLists } from "./queries";
import { ComposerDrafts } from "./composer";
import { conversationTitle } from "./local-input";
import { NewConversationDrafts, newConversationPath } from "./new-conversation";
import {
  ParticipantAvatars,
  threadParticipants,
} from "../shell/participant-avatars";
import styles from "./conversation.module.css";
import { useResults } from "./results";
import { useUnsent } from "./unsent";
import { LeadIcon } from "./lead-icon";

function threadState(row: ActivityRow) {
  if (row.pending_decision) return "Needs your answer";
  if (row.thread.root_activity.state === "preparing") return "Preparing";
  if (row.thread.root_activity.state === "running") return "Running";
  if (row.latest_operation?.status === "failed") return "Failed";
  if (row.thread.archived) return "Archived";
  return "";
}

function ThreadStateIcon({ row }: { row: ActivityRow }) {
  if (row.pending_decision)
    return <Question className={styles.threadWaiting} aria-hidden="true" />;
  if (row.thread.root_activity.state !== "inactive")
    return <CircleNotch className={styles.threadRunning} aria-hidden="true" />;
  if (row.latest_operation?.status === "failed")
    return (
      <WarningCircleIcon className={styles.threadFailed} aria-hidden="true" />
    );
  return row.thread.archived ? (
    <Archive aria-hidden="true" />
  ) : (
    <ChatCircle aria-hidden="true" />
  );
}

type ActivityRow = Pick<Schema<"ThreadActivityView">, "thread"> &
  Partial<Schema<"ThreadActivityView">>;

export function ThreadRow({
  row,
  presence,
  showRestore = false,
  showProject = false,
  projectLead = false,
}: {
  row: ActivityRow;
  presence: Schema<"PresenceFrame"> | null;
  showRestore?: boolean;
  showProject?: boolean;
  projectLead?: boolean;
}) {
  const { tracker: results } = useResults();
  const unsent = useUnsent().inputs.has(row.thread.thread_id);
  const unread = results?.isUnread(row.thread.thread_id);
  const navigate = useNavigate();
  const location = useLocation();
  const transport = useTransport();
  const queries = useQueryClient();
  const newDrafts = useContext(NewConversationDrafts);
  const composers = useContext(ComposerDrafts);
  const title = conversationTitle(
    row.thread,
    composers.get(row.thread.thread_id)?.localInputs,
  );
  const archive = useMutation({
    mutationFn: () =>
      result(
        transport.client.PATCH("/api/threads/{thread_id}/metadata", {
          params: { path: { thread_id: row.thread.thread_id } },
          body: {
            expected_version: row.thread.metadata_version,
            patch: { archived: !row.thread.archived },
          },
        }),
      ),
    onSuccess: () => {
      if (!row.thread.archived) newDrafts.detachArchived(row.thread.thread_id);
      if (
        !row.thread.archived &&
        location.pathname ===
          `/threads/${encodeURIComponent(row.thread.thread_id)}`
      )
        navigate(newConversationPath(row.thread.configuration.project_id));
    },
    onSettled: () => {
      void queries.invalidateQueries({
        queryKey: ["thread", row.thread.thread_id],
      });
      void refreshThreadLists(queries);
      void queries.invalidateQueries({ queryKey: ["unsent-threads"] });
    },
  });
  return (
    <div>
      <div key={row.thread.thread_id} className={styles.threadRow}>
        <NavLink
          to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
          className={({ isActive }) =>
            `${styles.threadLink} ${isActive ? styles.selected : ""}`
          }
        >
          {projectLead ? <LeadIcon size={18} /> : <ThreadStateIcon row={row} />}
          <span>
            <strong title={title}>{title}</strong>
            {projectLead && title !== "Coordinator" && (
              <small>Coordinator</small>
            )}
            {showProject && (
              <small>
                {row.project_name ??
                  (row.thread.configuration.project_id
                    ? "Unavailable project"
                    : "Without a project")}
              </small>
            )}
            {threadState(row) && <small>{threadState(row)}</small>}
          </span>
          {unsent && (
            <span className={styles.unsentMarker} title="Shared, unsent input">
              Draft
            </span>
          )}
          {unread && (
            <span
              className={styles.resultDot}
              role="img"
              aria-label="New result"
              title="New result"
            />
          )}
        </NavLink>
        <ParticipantAvatars
          participants={threadParticipants(presence, row.thread.thread_id)}
          ownId={presence?.participant_id}
          threadTitle={title}
        />
        {showRestore && row.thread.archived && (
          <Button
            variant="ghost"
            size="sm"
            loading={archive.isPending}
            disabled={row.thread.root_activity.state !== "inactive"}
            onClick={() => archive.mutate()}
            aria-label={`Restore ${title}`}
          >
            <ArrowCounterClockwise />
            Restore
          </Button>
        )}
        <Menu>
          <MenuTrigger
            render={<Button variant="ghost" size="icon-sm" />}
            className={styles.threadActions}
            aria-label={`Actions for ${title}`}
          >
            <DotsThree />
          </MenuTrigger>
          <MenuPopup align="start" side="right">
            {(
              [
                ["rename", "Rename conversation", PencilSimple],
                ["share", "Share conversation", ShareNetwork],
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
            <MenuItem
              disabled={
                archive.isPending ||
                row.thread.root_activity.state !== "inactive"
              }
              onClick={() => archive.mutate()}
            >
              <Archive />
              {row.thread.archived
                ? "Restore conversation"
                : "Archive conversation"}
            </MenuItem>
          </MenuPopup>
        </Menu>
      </div>
      <ErrorNotice error={archive.error} />
    </div>
  );
}
