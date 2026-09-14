import { Avatar, AvatarFallback, AvatarImage } from "a13n-ui";

const colors = [
  "#4f46e5",
  "#7c3aed",
  "#a21caf",
  "#be185d",
  "#be123c",
  "#c2410c",
  "#b45309",
  "#4d7c0f",
  "#047857",
  "#0e7490",
  "#0369a1",
  "#1d4ed8",
];

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
  let hash = 2166136261;
  for (const character of id ?? name) {
    hash = Math.imul(hash ^ character.codePointAt(0)!, 16777619) >>> 0;
  }
  const initial = name.match(/[\p{L}\p{N}]/u)?.[0].toUpperCase() ?? "A";
  return (
    <Avatar className={className} aria-hidden="true">
      {url && <AvatarImage src={url} alt="" />}
      <AvatarFallback
        className="rounded-[inherit] font-semibold text-white"
        style={{ backgroundColor: colors[hash % colors.length] }}
      >
        {initial}
      </AvatarFallback>
    </Avatar>
  );
}
