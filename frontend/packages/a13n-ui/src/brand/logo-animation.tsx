import type { ComponentProps } from "react";
import styles from "./logo-animation.module.css";
import { markColors, markGeometry } from "./mark";

export type LogoAnimationProps = Omit<ComponentProps<"span">, "children"> & {
  size?: number;
};

export function LogoAnimation({
  size = 32,
  className = "",
  style,
  ...props
}: LogoAnimationProps) {
  return (
    <span
      aria-hidden="true"
      {...props}
      className={`${styles.animation} ${className}`}
      style={{ width: size, height: size, ...style }}
    >
      <MarkLayer className={styles.shadow} fill={markColors.shadow} />
      <MarkLayer
        className={styles.face}
        fill={markColors.face}
        cutout={markColors.cutout}
      />
    </span>
  );
}

// Each layer is centered on the mark so it rotates about its own center.
function MarkLayer({
  className,
  fill,
  cutout,
}: {
  className: string;
  fill: string;
  cutout?: string;
}) {
  return (
    <svg
      className={className}
      viewBox={markGeometry.viewBox}
      aria-hidden="true"
    >
      <g fill={fill}>
        <Bars {...markGeometry.bar} />
      </g>
      {cutout && (
        <g fill={cutout}>
          <Bars {...markGeometry.cutout} />
        </g>
      )}
    </svg>
  );
}

function Bars(bar: (typeof markGeometry)["bar" | "cutout"]) {
  const { center } = markGeometry;
  return markGeometry.angles.map((angle) => (
    <rect
      key={angle}
      {...bar}
      transform={`rotate(${angle} ${center} ${center})`}
    />
  ));
}
