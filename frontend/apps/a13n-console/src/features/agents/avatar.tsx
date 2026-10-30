import { Avatar, AvatarFallback, AvatarImage } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { avatarTint, nameInitials } from "../../shared/identity";

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
  // Until an image loads or fails, the tile stays blank: initials would flash first.
  const [failed, setFailed] = useState<string>();
  const waiting = !!url && failed !== url;
  const fail = () => setFailed(url ?? undefined);
  return (
    <Avatar className={className} aria-hidden="true">
      {url &&
        (url.startsWith("blob:") ? (
          <AvatarImage
            src={url}
            alt=""
            onLoadingStatusChange={(status) => status === "error" && fail()}
          />
        ) : (
          <WorkspaceAvatarImage url={url} onError={fail} />
        ))}
      <AvatarFallback
        className="rounded-[inherit] font-semibold"
        style={waiting ? undefined : avatarTint(id ?? name)}
      >
        {waiting ? null : nameInitials(name, 1) || "A"}
      </AvatarFallback>
    </Avatar>
  );
}

/** Browser image requests cannot carry the workspace header. */
function WorkspaceAvatarImage({
  url,
  onError,
}: {
  url: string;
  onError: () => void;
}) {
  const client = useClient();
  const { workspace } = useWorkspace();
  const image = useQuery({
    queryKey: ["agent-avatar", workspace.id, url],
    queryFn: ({ signal }) => client.workspaceBlob(workspace.id, url, signal),
    staleTime: Infinity,
    gcTime: 60_000,
  });
  const [preview, setPreview] = useState<{ blob: Blob; url: string }>();
  useEffect(() => {
    if (!image.data) return;
    const objectUrl = URL.createObjectURL(image.data);
    setPreview({ blob: image.data, url: objectUrl });
    return () => URL.revokeObjectURL(objectUrl);
  }, [image.data]);
  const failed = image.isError;
  useEffect(() => {
    if (failed) onError();
  }, [failed, onError]);
  return preview && preview.blob === image.data ? (
    <AvatarImage
      src={preview.url}
      alt=""
      onLoadingStatusChange={(status) => status === "error" && onError()}
    />
  ) : null;
}
