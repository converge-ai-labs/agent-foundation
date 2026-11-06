// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { i18n } from "../i18n";
import {
  BrowserInstallInstructions,
  BrowserRequirements,
} from "./requirements";

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

it("translates known setup labels while preserving control keys, paths, commands and diagnostics", async () => {
  const command = "sudo apt-get install --no-install-recommends openbox";
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  render(
    <>
      <BrowserRequirements
        requirements={[
          {
            name: "VNC server",
            installed: true,
            detail: "/opt/服务器/Xvnc",
            purpose:
              "Streams the private browser desktop and accepts human keyboard and pointer input.",
          },
          {
            name: "Custom diagnostic",
            installed: false,
            detail: "vendor failure: X-42",
          },
        ]}
        controls={{ "VNC server": <button>Selected adapter</button> }}
      />
      <BrowserInstallInstructions
        steps={[{ title: "Optional: add window management", command }]}
      />
    </>,
  );
  fireEvent.click(screen.getByText("Manual installation commands"));
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(screen.getByText("VNC 服务器")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Selected adapter" })).toBeTruthy();
  expect(screen.getByText("Custom diagnostic")).toBeTruthy();
  expect(screen.getByText("vendor failure: X-42")).toBeTruthy();
  expect(screen.getByText(command)).toBeTruthy();
  await act(async () => {
    fireEvent.click(
      screen.getByRole("button", { name: "复制“可选：添加窗口管理”命令" }),
    );
  });
  expect(writeText).toHaveBeenCalledWith(command);
  expect(screen.getByText("已复制")).toBeTruthy();
});
