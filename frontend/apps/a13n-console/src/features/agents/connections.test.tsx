import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionGroup } from "./connections";

vi.mock("../../auth/context", () => ({ useClient: () => ({}) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String(values?.[name] ?? name),
      ),
  }),
}));
afterEach(cleanup);

it("removes an unavailable attachment while collapsed without changing tool permissions", () => {
  const onRemove = vi.fn();
  const onChange = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (readOnly: boolean) => (
    <QueryClientProvider client={cache}>
      <ConnectionGroup
        selection={{
          connection_id: "conn_missing",
          tools: ["lookup"],
          permissions: { lookup: "deny" },
        }}
        readOnly={readOnly}
        onChange={onChange}
        onRemove={onRemove}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view(false));
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove conn_missing" }));
  expect(onRemove).toHaveBeenCalledOnce();
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.queryByText("Default tool permission")).toBeNull();
  rerender(view(true));
  expect(
    screen.queryByRole("button", { name: "Remove conn_missing" }),
  ).toBeNull();
});
