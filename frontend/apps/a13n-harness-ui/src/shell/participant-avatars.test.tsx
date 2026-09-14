// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ParticipantAvatars, threadParticipants } from "./participant-avatars";
import type { Schema } from "../transport/client";

afterEach(cleanup);
const participant = (
  id: string,
  overrides: Partial<Schema<"ParticipantPresence">> = {},
): Schema<"ParticipantPresence"> => ({
  participant_id: id,
  display_name: "Alice",
  color: "#2563eb",
  foreground: true,
  availability: "available",
  focus: { target: { kind: "conversation", thread_id: "thread-one" } },
  ...overrides,
});

it("groups by root Thread context, retaining distinct tabs and separating inactive and unavailable members", () => {
  const peers = [
    participant("away", { foreground: false }),
    participant("own"),
    participant("same-name"),
    participant("file", {
      focus: {
        root_thread_id: "thread-one",
        target: { kind: "file", path: "/notes" },
      },
    }),
    participant("other", {
      focus: { target: { kind: "conversation", thread_id: "thread-other" } },
    }),
    participant("missing", { availability: "unavailable" }),
  ];
  const frame = { participants: peers, participant_id: "own" };
  expect(
    threadParticipants(frame, "thread-one").map((item) => item.participant_id),
  ).toEqual(["file", "own", "same-name", "away"]);
  expect(threadParticipants(null, "thread-one")).toEqual([]);
  expect(threadParticipants({ ...frame, closed: true }, "thread-one")).toEqual(
    [],
  );
});

it("shows a bounded avatar stack and all names on hover without navigating", async () => {
  const user = userEvent.setup();
  const peers = [
    participant("own"),
    participant("bob", { display_name: "Bob", foreground: false }),
    participant("same-name"),
    participant("long", {
      display_name: "A very long collaborator name that must wrap",
    }),
    participant("anonymous", { display_name: "" }),
  ];
  render(
    <ParticipantAvatars
      participants={peers}
      ownId="own"
      threadTitle="Design review"
    />,
  );
  const trigger = screen.getByRole("button", {
    name: "5 people online in Design review",
  });
  expect(trigger.textContent).toContain("+2");
  await user.hover(trigger);
  await screen.findByText("In this conversation");
  expect(screen.getByText("Bob")).toBeTruthy();
  expect(screen.getByText("(you)")).toBeTruthy();
  expect(screen.getByText(/Away/)).toBeTruthy();
  expect(screen.getByText("Anonymous")).toBeTruthy();
  expect(screen.getAllByRole("listitem")).toHaveLength(5);
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(screen.queryByText("In this conversation")).toBeNull(),
  );
});

it("supports keyboard opening and removes the stack when disconnected", async () => {
  const user = userEvent.setup();
  const { rerender } = render(
    <ParticipantAvatars
      participants={[participant("own")]}
      ownId="own"
      threadTitle="Chat"
    />,
  );
  await user.tab();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "1 person online in Chat" }),
  );
  await user.keyboard("{Enter}");
  await screen.findByText("In this conversation");
  rerender(<ParticipantAvatars participants={[]} threadTitle="Chat" />);
  expect(screen.queryByRole("button")).toBeNull();
});
