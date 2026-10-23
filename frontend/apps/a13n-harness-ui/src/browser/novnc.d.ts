declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      url: string,
      options?: { credentials: { password: string } },
    );
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    focusOnClick: boolean;
    clipboardPasteFrom(text: string): void;
    getImageData(): ImageData;
    focus(): void;
    disconnect(): void;
    sendKey(keysym: number, code?: string, down?: boolean): void;
  }
}
