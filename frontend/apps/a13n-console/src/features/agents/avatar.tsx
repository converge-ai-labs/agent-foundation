import { Avatar, AvatarFallback, AvatarImage } from "a13n-ui";
import { Bot } from "lucide-react";

export function AgentAvatar({
  url,
  className,
}: {
  url?: string | null;
  className?: string;
}) {
  return (
    <Avatar className={`bg-muted text-muted-foreground ${className ?? ""}`}>
      {url && <AvatarImage src={url} alt="" />}
      <AvatarFallback className="rounded-[inherit] bg-transparent">
        <Bot className="size-5" size={18} strokeWidth={1.5} />
      </AvatarFallback>
    </Avatar>
  );
}
