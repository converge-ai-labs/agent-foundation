declare module "pptx-browser" {
  export default class PptxRenderer {
    load(bytes: ArrayBuffer): Promise<void>;
    slideCount: number;
    slideSize: { cx: number; cy: number };
    renderSlide(
      index: number,
      canvas: HTMLCanvasElement,
      width: number,
    ): Promise<void>;
    destroy(): void;
  }
}
