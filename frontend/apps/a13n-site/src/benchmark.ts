import { $$ } from "./dom";

/** The SVG owns the values; the selector makes even overlapping points inspectable. */
export function mountBenchmarkInspectors() {
  for (const svg of $$<SVGSVGElement>(".benchmark-comparison")) {
    const points = $$<SVGGElement>("[data-result]", svg);
    const box = document.createElement("div");
    box.className = "benchmark-inspector";
    const label = document.createElement("label");
    const select = document.createElement("select");
    label.append("Inspect result ", select);
    const output = document.createElement("output");
    const note = document.createElement("p");
    note.className = "benchmark-note";
    const update = (index: number) => {
      select.value = String(index);
      points.forEach((point, i) => {
        point.classList.toggle("selected", i === index);
      });
      output.textContent = points[index].dataset.summary!;
      note.textContent = points[index].dataset.note!;
    };
    points.forEach((point, index) => {
      select.add(new Option(point.dataset.result!, String(index)));
      point.addEventListener("click", () => update(index));
    });
    select.addEventListener("change", () => update(Number(select.value)));
    box.append(label, output, note);
    svg.closest(".fig-scroll")!.after(box);
    update(0);
  }
}
