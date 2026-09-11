// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { useSuggestedName } from "./suggested-name";

it("follows provider selections until the name is manually edited", () => {
  const { result } = renderHook(() => useSuggestedName());
  act(() => result.current.suggestName("Composio"));
  expect(result.current.name).toBe("Composio");
  act(() => result.current.suggestName("Other provider"));
  expect(result.current.name).toBe("Other provider");
  act(() => result.current.setName("My account"));
  act(() => result.current.suggestName("Composio"));
  expect(result.current.name).toBe("My account");
  act(() => result.current.setName(""));
  act(() => result.current.suggestName("Other provider"));
  expect(result.current.name).toBe("");
});

it("preserves the existing name when editing a provider", () => {
  const { result } = renderHook(() => useSuggestedName("Existing provider"));
  act(() => result.current.suggestName("Composio"));
  expect(result.current.name).toBe("Existing provider");
});
