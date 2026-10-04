import { $ } from "./dom";
import { turn } from "./mark";

/** The nav bar: the brand turns on hover; the bar fills in past the hero and darkens over the night. */
export class Nav {
  private readonly el = $("#nav");

  constructor() {
    const brand = $(".brand", this.el);
    const mark = $(".mk", brand);
    brand.addEventListener("pointerenter", () => turn(mark));
  }

  tick(dark: boolean) {
    this.el.classList.toggle("solid", scrollY > innerHeight * 0.55);
    this.el.classList.toggle("dark", dark);
  }
}
