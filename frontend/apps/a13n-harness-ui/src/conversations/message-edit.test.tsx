// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router";
import { TransportContext } from "../transport/context";
import { ApiError, NetworkError, type Transport } from "../transport/client";
import { ConnectionNoticeContext } from "../shell/connection";
import { ThreadDraft } from "./draft";
import { InputContent, type InputPart } from "./input-content";
import { MessageBranchProvider } from "./message-branch";
import { SendShortcutContext } from "./send-shortcut";
import { ConversationTranscript } from "./transcript";

const source = "input_" + "a".repeat(32);
const receipt = "receipt-" + "b".repeat(32);
const continuation = "c".repeat(64);
const turn = "active-input";
const inputMetadata = (index: number, attachment?: string) => ({
  source_id: source,
  harness_ui: {
    composer: { index },
    ...(attachment && {
      attachment: {
        attachment_id: attachment,
        name: `${attachment}.txt`,
        media_type: "text/plain",
        size: 5,
      },
    }),
  },
});
const original: InputPart[] = [
  { kind: "user", text: "Try $local-skill ", metadata: inputMetadata(0) },
  {
    kind: "user",
    text: "[generated instructions]",
    metadata: inputMetadata(1, "keep"),
  },
  { kind: "user", text: "later", metadata: inputMetadata(2) },
  {
    kind: "user",
    text: "[generated instructions]",
    metadata: inputMetadata(3, "remove"),
  },
  {
    kind: "user",
    text: "[hidden skill instructions]",
    metadata: { display: false },
  },
];

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Location() {
  const value = useLocation();
  return <span data-testid="location">{value.pathname}</span>;
}

function setup({
  active = true,
  shortcut = "enter" as "enter" | "mod-enter",
  parts = original,
  checkpointed = false,
} = {}) {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const post = vi.fn().mockResolvedValue({
    data: {
      receipt: { thread_id: "one", receipt_id: "new" },
      continuation_id: "rewound",
    },
  });
  const get = vi.fn((path) =>
    Promise.resolve(
      path.endsWith("/skills")
        ? {
            data: {
              catalog_id: "catalog",
              items: [
                {
                  item_id: "skill-one",
                  source_id: "test",
                  name: "local-skill",
                },
              ],
            },
          }
        : {
            data: {
              source: continuation,
              edit_source: active ? receipt : continuation,
              points: {
                [checkpointed ? source : turn]: { edit: true, branch: false },
              },
            },
          },
    ),
  );
  const transport = {
    client: { GET: get, POST: post },
  } as unknown as Transport;
  const draft = new ThreadDraft();
  const rewound = vi.fn();
  const pending = vi.fn();
  const view = (
    visible: boolean,
    checkpoint = continuation,
    runId?: string,
  ) => (
    <TransportContext value={transport}>
      <QueryClientProvider client={queries}>
        <MemoryRouter initialEntries={["/threads/one"]}>
          <ConnectionNoticeContext value>
            <SendShortcutContext value={shortcut}>
              <Location />
              <MessageBranchProvider
                thread="one"
                continuation={checkpoint}
                receipt={active ? receipt : undefined}
                runId={runId}
                active={active}
                draft={draft}
                blocked={false}
                enabled
                onEditPending={pending}
                onRewound={rewound}
              >
                {visible &&
                  (checkpointed ? (
                    <ConversationTranscript
                      threadId="one"
                      running={active}
                      localInputs={[]}
                      blocks={[]}
                      entries={[
                        {
                          position: 0,
                          message_kind: "request",
                          parts: [
                            {
                              kind: "user",
                              text: "Checkpointed input",
                              metadata: inputMetadata(0),
                            },
                          ],
                        },
                      ]}
                      turns={[
                        {
                          turn_id: "model-attempt-key",
                          input_position: 0,
                          end_position: 1,
                          preview: "Checkpointed input",
                        },
                      ]}
                    />
                  ) : (
                    <InputContent
                      branchKey={turn}
                      parts={parts}
                      renderText={(text) => <span>{text}</span>}
                    />
                  ))}
              </MessageBranchProvider>
            </SendShortcutContext>
          </ConnectionNoticeContext>
        </MemoryRouter>
      </QueryClientProvider>
    </TransportContext>
  );
  const rendered = render(view(true));
  return {
    get,
    post,
    rewound,
    pending,
    hideSource: () => rendered.rerender(view(false)),
    checkpoint: (revision: string, runId?: string) =>
      rendered.rerender(view(true, revision, runId)),
  };
}

async function openEditor() {
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "Edit message" }));
  return screen.getByRole<HTMLTextAreaElement>("textbox", {
    name: "Edit message",
  });
}

it("opens during a Run without sending Stop, and Escape and Cancel both discard only the local edit", async () => {
  const { post, pending } = setup();
  for (const cancel of ["escape", "click"]) {
    const editor = await openEditor();
    expect(editor.value).toBe("Try $local-skill later");
    expect(editor.selectionStart).toBe(editor.value.length);
    await waitFor(() => expect(document.activeElement).toBe(editor));
    expect(screen.getByRole("button", { name: "Stop & send" })).toBeTruthy();
    if (cancel === "escape") fireEvent.keyDown(editor, { key: "Escape" });
    else fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: "Edit message" })).toBeNull();
  }
  expect(post).not.toHaveBeenCalled();
  expect(pending).not.toHaveBeenCalled();
  expect(screen.getByTestId("location").textContent).toBe("/threads/one");
});

it("keeps the current Run's input editable after it enters saved history with a different turn identity", async () => {
  const { post, rewound } = setup({ checkpointed: true });
  const editor = await openEditor();
  expect(editor.value).toBe("Checkpointed input");
  fireEvent.click(screen.getByRole("button", { name: "Stop & send" }));
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(post.mock.calls[0][1].body.selection).toEqual({
    expected_source: receipt,
    turn_key: source,
  });
  expect(rewound.mock.calls[0][2].inputPosition).toBe(0);
});

it("refreshes branch points when the active Run starts but not at its later checkpoints", async () => {
  const { get, checkpoint } = setup();
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  checkpoint("first", "run-one");
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  checkpoint("second", "run-one");
  checkpoint("third", "run-one");
  await screen.findByRole("button", { name: "Edit message" });
  expect(get).toHaveBeenCalledTimes(2);
});

it("sends once in place, keeping the original order, retained attachment and current Skill reference", async () => {
  const { post, get, rewound, pending } = setup();
  const editor = await openEditor();
  fireEvent.change(editor, { target: { value: "Apply $local-skill later" } });
  fireEvent.click(screen.getByRole("button", { name: "Remove remove.txt" }));
  fireEvent.click(screen.getByRole("button", { name: "Stop & send" }));
  await waitFor(() => expect(rewound).toHaveBeenCalledOnce());
  expect(post).toHaveBeenCalledOnce();
  expect(post).toHaveBeenCalledWith("/api/threads/{thread_id}/edit-message", {
    params: { path: { thread_id: "one" } },
    body: {
      selection: { expected_source: receipt, turn_key: turn },
      parts: ["Apply $local-skill ", { attachment_id: "keep" }, "later"],
      source_id: expect.stringMatching(/^input_[a-f0-9]{32}$/),
      skill_references: [
        { catalog_id: "catalog", item_id: "skill-one", name: "local-skill" },
      ],
    },
  });
  expect(post.mock.calls[0][1].body.source_id).not.toBe(source);
  expect(get).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/skills",
    expect.anything(),
  );
  expect(pending.mock.calls).toEqual([[true], [false]]);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByTestId("location").textContent).toBe("/threads/one");
});

it.each(["enter", "mod-enter"] as const)(
  "honors %s and ignores composing Enter",
  async (shortcut) => {
    const { post, rewound } = setup({
      active: false,
      shortcut,
      parts: [{ kind: "user", text: "Original" }],
    });
    const editor = await openEditor();
    fireEvent.change(editor, { target: { value: "Replacement" } });
    fireEvent.keyDown(editor, {
      key: "Enter",
      isComposing: true,
      ctrlKey: shortcut === "mod-enter",
    });
    expect(post).not.toHaveBeenCalled();
    if (shortcut === "mod-enter") {
      fireEvent.keyDown(editor, { key: "Enter" });
      expect(post).not.toHaveBeenCalled();
    }
    fireEvent.keyDown(editor, {
      key: "Enter",
      ctrlKey: shortcut === "mod-enter",
    });
    await waitFor(() => expect(rewound).toHaveBeenCalledOnce());
    expect(post.mock.calls[0][1].body).toMatchObject({
      selection: { expected_source: continuation, turn_key: turn },
      parts: ["Replacement"],
    });
  },
);

it.each([
  [
    new ApiError("The active run did not stop in time.", 409),
    "The active run did not stop in time.",
  ],
  [
    new ApiError("Conversation history changed. Reopen the message.", 409),
    "Conversation history changed. Reopen the message.",
  ],
  [
    new NetworkError(new Error("connection reset")),
    "Could not confirm the edit. Check the conversation before sending again.",
  ],
] as const)(
  "keeps edits and displays the rejection (%s)",
  async (failure, message) => {
    const { post, rewound, hideSource } = setup({
      parts: [{ kind: "user", text: "Original" }],
    });
    let reject!: (error: unknown) => void;
    post.mockImplementationOnce(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail;
        }),
    );
    const editor = await openEditor();
    fireEvent.change(editor, { target: { value: "Keep this changed text" } });
    fireEvent.click(screen.getByRole("button", { name: "Stop & send" }));
    expect(screen.getByRole("status").textContent).toBe("Sending edit…");
    expect(editor.readOnly).toBe(true);
    if (failure instanceof NetworkError) hideSource();
    await act(async () => reject(failure));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(
      screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Edit message" })
        .value,
    ).toBe("Keep this changed text");
    expect(rewound).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledOnce();
  },
);

it("does not offer editing for a file derived from a long user input", async () => {
  const { get } = setup({
    parts: [
      {
        kind: "user",
        text: "Derived file",
        metadata: { harness_ui: { long_text: true } },
      },
    ],
  });
  await waitFor(() => expect(get).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Edit message" })).toBeNull();
});
