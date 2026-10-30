import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProviderConfiguration } from "./provider-configuration";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
function schema(variant: "daytona" | "modal") {
  const field = variant === "modal" ? "image" : "snapshot";
  return {
    type: "object",
    required: [field],
    "x-primary-fields": ["desktop", field],
    properties: {
      [field]: { type: "string", title: "Image or snapshot", minLength: 1 },
      desktop: {
        type: "boolean",
        title: "Desktop environment",
        default: false,
      },
      desktop_assistance: {
        type: "boolean",
        title: "Desktop assistance and control",
        default: false,
      },
    },
  };
}
function Editor({ variant }: { variant: "daytona" | "modal" }) {
  const [text, setText] = useState(
    JSON.stringify({
      [variant === "modal" ? "image" : "snapshot"]: "my-envd",
      desktop: true,
      desktop_assistance: true,
    }),
  );
  return (
    <>
      <ProviderConfiguration
        variant={variant}
        schema={schema(variant)}
        text={text}
        onChange={setText}
      />
      <output data-testid="config">{text}</output>
    </>
  );
}
it.each(["daytona", "modal"] as const)(
  "%s clears human access when disabling desktop",
  async (variant) => {
    const user = userEvent.setup();
    render(<Editor variant={variant} />);
    await user.click(
      screen.getByRole("switch", { name: "Desktop environment" }),
    );
    expect(
      JSON.parse(screen.getByTestId("config").textContent ?? "{}"),
    ).toEqual({
      [variant === "modal" ? "image" : "snapshot"]: "my-envd",
      desktop: false,
      desktop_assistance: false,
    });
    expect(screen.queryByText("Desktop assistance and control")).toBeNull();
    expect(
      (
        screen.getByRole("textbox", {
          name: "Image or snapshot",
        }) as HTMLInputElement
      ).value,
    ).toBe("my-envd");
  },
);
