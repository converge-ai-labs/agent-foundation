import { Avatar, AvatarFallback, AvatarImage } from "a13n-ui";
import { avatarColor, nameInitials } from "../../shared/identity";

export function AgentAvatar({
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
    <Avatar className={className} aria-hidden="true">
      {url && <AvatarImage src={url} alt="" />}
      <AvatarFallback
        className="rounded-[inherit] font-semibold text-white"
        style={{ backgroundColor: avatarColor(id ?? name) }}
      >
        {nameInitials(name, 1) || "A"}
      </AvatarFallback>
    </Avatar>
  );
}
