import type { Schema } from "../transport/client";

export function selectedSpeedState(
  modelState: Schema<"FastControl">["state"] | undefined,
  override: Schema<"SubmitRequest">["fast"],
) {
  if (override == null) return modelState;
  if (override === "ultrafast") return "ultrafast";
  return override ? "on" : "off";
}

/** Display the request speed, independently of whether it was inherited. */
export function speedLabel(state?: Schema<"FastControl">["state"] | null) {
  switch (state) {
    case "on":
      return "Fast";
    case "ultrafast":
      return "Ultrafast";
    case "off":
    case "default":
      return "Standard";
    default:
      return "—";
  }
}
