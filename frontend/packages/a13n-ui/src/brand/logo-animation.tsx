import type { ComponentProps } from "react";
import styles from "./logo-animation.module.css";

// The a13n-logo.svg geometry: three rounded bars rotated around the mark center.
const BAR_ANGLES = [0, 60, -60];

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
      <MarkLayer className={styles.shadow} fill="#3730a3" />
      <MarkLayer className={styles.face} fill="#4f46e5" cutout="#fbfbfd" />
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
    <svg className={className} viewBox="167 167 690 690" aria-hidden="true">
      <g fill={fill}>
        {BAR_ANGLES.map((angle) => (
          <rect
            key={angle}
            x={417}
            y={236}
            width={190}
            height={552}
            rx={16}
            transform={`rotate(${angle} 512 512)`}
          />
        ))}
      </g>
      {cutout && (
        <g fill={cutout}>
          {BAR_ANGLES.map((angle) => (
            <rect
              key={angle}
              x={468}
              y={312}
              width={88}
              height={400}
              rx={7}
              transform={`rotate(${angle} 512 512)`}
            />
          ))}
        </g>
      )}
    </svg>
  );
}
