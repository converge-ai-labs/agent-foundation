declare module "@novnc/novnc/lib/rfb" {
  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      url: string,
      options: {
        credentials: { password: string };
        shared: boolean;
        wsProtocols?: string[];
      },
    );
    viewOnly: boolean;
    scaleViewport: boolean;
    disconnect(): void;
  }
}
