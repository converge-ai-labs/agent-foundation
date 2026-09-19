import { Avatar, AvatarFallback, AvatarImage } from "a13n-ui";
import { avatarColor, nameInitials } from "../shared/identity";

/**
 * A person, by image when they have one. Otherwise their initials on a hue
 * derived from their identity, so similar names stay distinguishable.
 */
export function UserAvatar({
  name,
  id,
  url,
  className,
}: {
  name: string;
  id?: string;
  url?: string | null;
  className?: string;
}) {
  return (
    <Avatar className={className}>
      {url && <AvatarImage src={url} alt="" />}
      <AvatarFallback
        className="rounded-[inherit] font-medium text-[11px] text-white"
        style={{ backgroundColor: avatarColor(id ?? name) }}
      >
        {nameInitials(name) || "?"}
      </AvatarFallback>
    </Avatar>
  );
}
