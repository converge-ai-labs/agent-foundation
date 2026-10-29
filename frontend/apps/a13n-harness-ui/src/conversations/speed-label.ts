import type { Schema } from "../transport/client";

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
