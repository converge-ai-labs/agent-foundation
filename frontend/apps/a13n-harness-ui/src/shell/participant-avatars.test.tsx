// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
  await screen.findByText("People here");
  rerender(<ParticipantAvatars participants={[]} threadTitle="Chat" />);
  expect(screen.queryByRole("button")).toBeNull();
});

it("changes language without closing the presence popover or translating participant names", async () => {
  const { i18n } = await import("../i18n");
  const user = userEvent.setup();
  try {
    render(
      <ParticipantAvatars
        participants={[participant("own", { display_name: "Anonymous" })]}
        ownId="own"
        threadTitle="My chat"
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "1 person online in My chat" }),
    );
    await screen.findByText("People here");
    await i18n.changeLanguage("zh-CN");
    await screen.findByText("在场人员");
    expect(screen.getByText("Anonymous")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "My chat 中有 1 人在线" }),
    ).toBeTruthy();
  } finally {
    cleanup();
    await i18n.changeLanguage("en");
  }
});

it("uses a generic label for unnamed pages and deduplicates only repeated participant identities", async () => {
  render(
    <ParticipantAvatars
      participants={[
        participant("same-tab"),
        participant("same-tab"),
        participant("distinct-tab"),
      ]}
      threadTitle="   "
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "People here (2)" }),
  );
  expect(await screen.findAllByRole("listitem")).toHaveLength(2);
});
