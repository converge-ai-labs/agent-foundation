import logo from "a13n-ui/brand/logo.svg";
import { Wordmark } from "a13n-ui/brand/wordmark";

/** The site title: the a13n mark and wordmark, then a quiet "Docs" label. */
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
      <span className="ms-3 text-[15px] text-fd-muted-foreground">Docs</span>
    </span>
  );
}

export { logo };
