// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Dictation } from "./dictation";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { recordAudio } from "./dictation-audio";
import { i18n } from "../i18n";
vi.mock("./dictation-audio", () => ({
  recordAudio: vi.fn(),
  encodeAudio: async () => "recorded-audio",
}));
afterEach(cleanup);
it("retries with the original audio and reading context and inserts the response once", async () => {
  await i18n.changeLanguage("en");
  const finish = vi.fn(async () => new Blob(["sample"]));
  vi.mocked(recordAudio).mockResolvedValue(finish);
  const post = vi
    .fn()
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValueOnce({ data: "Run Pyright." });
  const transport = { client: { POST: post } } as unknown as Transport;
  const insert = vi.fn(),
    onActive = vi.fn(),
    reader = document.createElement("div");
  reader.innerHTML = '<p data-reply-source="reply">Check Pyright now</p>';
  render(
    <TransportContext value={transport}>
      <Dictation
        target={{ title: "Type check", reader: { current: reader }, words: 4 }}
        quote={{ source: "reply", start: 6, text: "Pyright", raw: "Pyright" }}
        insert={insert}
        onActive={onActive}
      />
    </TransportContext>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Voice input" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Stop recording" }),
  );
  const retry = await screen.findByRole("button", { name: "Retry" });
  reader.textContent = "Different chat text";
  expect(insert).not.toHaveBeenCalled();
  fireEvent.click(retry);
  await waitFor(() => expect(insert).toHaveBeenCalledWith("Run Pyright."));
  expect(post.mock.calls[0][1].body.context).toContain(
    "Selected assistant text: Pyright",
  );
  expect(post.mock.calls[1][1].body).toEqual(post.mock.calls[0][1].body);
  expect(recordAudio).toHaveBeenCalledTimes(1);
  expect(finish).toHaveBeenCalledTimes(1);
});
