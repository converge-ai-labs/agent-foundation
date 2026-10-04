/** The a13n-logo.svg geometry: three rounded bars rotated around the mark center. */
export const markGeometry = {
  viewBox: "167 167 690 690",
  center: 512,
  angles: [0, 60, -60],
  bar: { x: 417, y: 236, width: 190, height: 552, rx: 16 },
  cutout: { x: 468, y: 312, width: 88, height: 400, rx: 7 },
} as const;

export const markColors = {
  shadow: "#3730a3",
  face: "#4f46e5",
  cutout: "#fbfbfd",
} as const;
