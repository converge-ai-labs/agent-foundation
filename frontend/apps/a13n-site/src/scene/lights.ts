import { context2d } from "../dom";

/** A point of light, drawn once and stamped onto canvases at any size. */
function light(rgb: string, ring = false) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 48;
  const g = context2d(canvas);
  if (ring) {
    g.strokeStyle = `rgba(${rgb},0.9)`;
    g.lineWidth = 3;
    g.beginPath();
    g.arc(24, 24, 12, 0, Math.PI * 2);
    g.stroke();
    return canvas;
  }
  const glow = g.createRadialGradient(24, 24, 0, 24, 24, 24);
  glow.addColorStop(0, "rgba(255,255,255,1)");
  glow.addColorStop(0.11, `rgba(${rgb},1)`);
  glow.addColorStop(0.24, `rgba(${rgb},0.1)`);
  glow.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = glow;
  g.fillRect(0, 0, 48, 48);
  return canvas;
}

/** A session at work, one moved to another worker, and one waiting for input. */
export const LIGHTS = {
  run: light("150,142,255"),
  moved: light("255,110,115"),
  wait: light("255,255,255", true),
};
