// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { ProjectFolders } from "./project-folders";
import { i18n } from "../i18n";

let sharing: boolean;
let finder: boolean;
let picked: string | null;
let failMore: boolean;
let reads: URL[];
let queries: QueryClient;
const submitted = vi.fn();
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function entry(path: string, kind = "directory") {
  return { path, kind, revision: "entry" };
}
beforeEach(() => {
  sharing = true;
  finder = false;
  picked = null;
  failMore = false;
  reads = [];
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/host/files/pick-directory") {
        expect(request.method).toBe("POST");
        return json(picked);
      }
      if (url.pathname === "/api/status")
        return json({
          features: {
            host_files: sharing,
            file_manager: finder ? "finder" : null,
          },
        });
      if (url.pathname === "/api/setup")
        return json({ suggested_project_path: "/srv" });
      if (url.pathname === "/api/host/files") {
        reads.push(url);
        const path = url.searchParams.get("path")!;
        const more = url.searchParams.get("offset") === "2";
        if (path === "/denied" || (more && failMore))
          return json(
            {
              error: {
                code: "host_files_conflict",
                message: "Directory changed. Refresh the listing.",
              },
            },
            409,
          );
        return json({
          directory: { ...entry(path), revision: "directory-v1" },
          resolved_path: path === "/srv/link" ? "/actual" : path,
          entries:
            path === "/srv"
              ? more
                ? [entry("/srv/link", "symlink")]
                : [entry("/srv/code"), entry("/srv/readme.md", "file")]
              : [],
          next_offset: path === "/srv" && !more ? 2 : null,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  void i18n.changeLanguage("en");
  queries.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
function mount() {
  function Fields() {
    const [roots, setRoots] = useState([{ path: "" }]);
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submitted();
        }}
      >
        <ProjectFolders roots={roots} onChange={setRoots} />
        <output aria-label="Roots">{JSON.stringify(roots)}</output>
      </form>
    );
  }
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <Fields />
      </TransportContext>
    </QueryClientProvider>,
  );
}
async function clickAdd() {
  const button = screen.getByRole("button", {
    name: "Add folder",
  }) as HTMLButtonElement;
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
}
it("adds a native selection to the draft and leaves it intact on cancellation", async () => {
  finder = true;
  mount();
  picked = "/chosen/workspace";
  await clickAdd();
  await screen.findByTitle("/chosen/workspace");
  picked = null;
  await clickAdd();
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Add folder" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect(screen.getByLabelText("Roots").textContent).toBe(
    '[{"path":"/chosen/workspace"}]',
  );
  expect(reads).toHaveLength(0);
});
it("browses a level at a time, paginates with a revision, follows links, and selects only explicitly", async () => {
  mount();
  await clickAdd();
  await screen.findByRole("button", { name: "code" });
  expect(screen.queryByRole("button", { name: "readme.md" })).toBeNull();
  expect(screen.getByLabelText("Roots").textContent).toBe('[{"path":""}]');
  fireEvent.click(screen.getByRole("button", { name: "code" }));
  await screen.findByText("No subfolders.");
  expect(reads.at(-1)?.searchParams.get("path")).toBe("/srv/code");
  fireEvent.click(screen.getByRole("button", { name: "Parent folder" }));
  await screen.findByRole("button", { name: "code" });
  fireEvent.click(screen.getByRole("button", { name: "Load more entries" }));
  await waitFor(() =>
    expect(reads.at(-1)?.searchParams.get("offset")).toBe("2"),
  );
  await screen.findByRole("button", { name: /link/ });
  expect(reads.at(-1)?.searchParams.get("revision")).toBe("directory-v1");
  fireEvent.click(screen.getByRole("button", { name: /link/ }));
  await screen.findByTitle("/actual");
  fireEvent.click(screen.getByRole("button", { name: "Use this directory" }));
  expect(
    screen.queryByRole("region", { name: "Browse server directories" }),
  ).toBeNull();
  expect(screen.getByLabelText("Roots").textContent).toBe(
    '[{"path":"/actual"}]',
  );
  expect(submitted).not.toHaveBeenCalled();
});
it("keeps manual rows usable without native sharing and removes only the selected root", async () => {
  sharing = false;
  mount();
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Add folder" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect(
    screen.queryByRole("button", { name: "Browse directory 1" }),
  ).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Server directory" }), {
    target: { value: "/one" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
  fireEvent.change(
    screen.getByRole("textbox", { name: "Additional server directory 1" }),
    { target: { value: "/two" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove directory 1" }));
  expect(screen.getByLabelText("Roots").textContent).toBe('[{"path":"/two"}]');
  expect(
    screen.queryByRole("button", { name: "Remove directory 1" }),
  ).toBeNull();
  expect(reads).toHaveLength(0);
  expect(submitted).not.toHaveBeenCalled();
});
it("restarts changed directory pagination and does not submit the project when entering a browse path", async () => {
  mount();
  const user = userEvent.setup();
  await clickAdd();
  await screen.findByRole("button", { name: "code" });
  failMore = true;
  fireEvent.click(screen.getByRole("button", { name: "Load more entries" }));
  await screen.findByText("Directory changed. Refresh the listing.");
  failMore = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(reads.at(-1)?.searchParams.get("offset")).toBe("0"),
  );
  await user.clear(screen.getByRole("textbox", { name: "Browse server path" }));
  await user.type(
    screen.getByRole("textbox", { name: "Browse server path" }),
    "/denied{Enter}",
  );
  await screen.findByText("Directory changed. Refresh the listing.");
  expect(
    (
      screen.getByRole("button", {
        name: "Use this directory",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.getByLabelText("Roots").textContent).toBe('[{"path":""}]');
  expect(submitted).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(
    screen.queryByRole("region", { name: "Browse server directories" }),
  ).toBeNull();
});

it("keeps the open directory browser and draft paths when the language changes", async () => {
  mount();
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Server directory" }),
    {
      target: { value: "/srv" },
    },
  );
  await clickAdd();
  await screen.findByRole("button", { name: "code" });
  fireEvent.change(
    screen.getByRole("textbox", { name: "Browse server path" }),
    {
      target: { value: "/draft/path" },
    },
  );
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(
    screen.getByRole("region", { name: i18n.t("Browse server directories") }),
  ).toBeTruthy();
  expect(
    (
      screen.getByRole("textbox", {
        name: i18n.t("Browse server path"),
      }) as HTMLInputElement
    ).value,
  ).toBe("/draft/path");
  expect(screen.getByRole("button", { name: "code" })).toBeTruthy();
  expect(screen.getByLabelText("Roots").textContent).toBe('[{"path":"/srv"}]');
});
