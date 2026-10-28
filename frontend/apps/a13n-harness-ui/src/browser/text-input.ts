import type RFB from "@novnc/novnc";
import styles from "./panel.module.css";

/** Give the local IME an editable target while noVNC handles physical keys. */
export function bindTextInput({
  root,
  active,
  connection,
  commit,
}: {
  root: HTMLElement;
  active: () => boolean;
  connection: Pick<RFB, "focusOnClick">;
  commit: (text: string) => void;
}) {
  const input = document.createElement("textarea");
  input.className = styles.textInput;
  input.setAttribute("aria-label", "Remote browser keyboard input");
  input.autocomplete = "off";
  input.autocapitalize = "off";
  input.spellcheck = false;
  root.append(input);
  const canvas = root.querySelector("canvas")!;
  let composing = false;
  const focus = () => {
    if (active()) input.focus({ preventScroll: true });
  };
  const clear = () => {
    composing = false;
    input.removeAttribute("data-composing");
    input.value = "";
  };
  const send = (text: string) => {
    if (active() && text) commit(text);
    clear();
  };
  const start = () => {
    composing = true;
    input.setAttribute("data-composing", "");
  };
  const end = (event: CompositionEvent) => send(event.data);
  const text = (event: Event) => {
    const { isComposing, inputType } = event as InputEvent;
    // Chrome updates before compositionend; Safari may emit a final input
    // afterwards. Only compositionend commits the IME text, exactly once.
    if (composing || isComposing || inputType.includes("Composition")) return;
    send(input.value);
  };
  const key = (event: KeyboardEvent) => {
    if (event.target !== input) return;
    if (!active()) {
      event.preventDefault();
      event.stopImmediatePropagation();
    } else if (
      composing ||
      event.isComposing ||
      event.keyCode === 229 ||
      event.key === "Dead"
    ) {
      // Keep the IME's default action, but do not leak preedit keys to noVNC.
      event.stopImmediatePropagation();
    } else {
      // Preserve noVNC's own layout, AltGr and lock-key handling on its canvas.
      canvas.dispatchEvent(new KeyboardEvent(event.type, event));
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };
  const pointer = (event: PointerEvent) => {
    if (!active()) return;
    const bounds = root.getBoundingClientRect();
    input.style.left = `${Math.max(0, Math.min(event.clientX - bounds.left, bounds.width - input.offsetWidth))}px`;
    input.style.top = `${Math.max(0, Math.min(event.clientY - bounds.top, bounds.height - input.offsetHeight))}px`;
    focus();
  };
  root.addEventListener("keydown", key, true);
  root.addEventListener("keyup", key, true);
  input.addEventListener("compositionstart", start);
  input.addEventListener("compositionend", end);
  input.addEventListener("input", text);
  input.addEventListener("blur", clear);
  root.addEventListener("pointerdown", pointer);
  // noVNC must not steal focus back from the editable IME target.
  connection.focusOnClick = false;
  return {
    focus,
    stop: () => {
      root.removeEventListener("keydown", key, true);
      root.removeEventListener("keyup", key, true);
      root.removeEventListener("pointerdown", pointer);
      input.removeEventListener("compositionstart", start);
      input.removeEventListener("compositionend", end);
      input.removeEventListener("input", text);
      input.removeEventListener("blur", clear);
      clear();
      input.remove();
    },
  };
}
