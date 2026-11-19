import logo from "a13n-ui/brand/logo.svg";
import { Wordmark } from "a13n-ui/brand/wordmark";

/** The a13n mark and wordmark for the website link. */
export function NavTitle() {
  return (
    <span className="flex items-center">
      <img
        src={logo.src}
        alt=""
        width={22}
        height={22}
        className="size-[22px]"
      />
      <Wordmark className="ms-2 text-[18px] text-fd-foreground" />
    </span>
  );
}

export { logo };
