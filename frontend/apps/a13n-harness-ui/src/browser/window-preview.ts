/** Read-only JPEG delivery. Decode at most one frame and retain only the latest. */
export function connectWindowPreview(
  url: string,
  canvas: HTMLCanvasElement,
  rendered: () => void,
  failed: () => void,
): () => void {
  const socket = new WebSocket(url);
  let closed = false;
  let decoding = false;
  let latest: Blob | null = null;
  const close = () => {
    closed = true;
    latest = null;
    socket.close();
    canvas.width = canvas.height = 0;
  };
  const fail = () => {
    if (closed) return;
    close();
    failed();
  };
  const draw = async () => {
    decoding = true;
    try {
      while (latest && !closed) {
        const frame = latest;
        latest = null;
        const image = await createImageBitmap(frame);
        try {
          if (closed) return;
          canvas.width = image.width;
          canvas.height = image.height;
          canvas.getContext("2d")!.drawImage(image, 0, 0);
          rendered();
        } finally {
          image.close();
        }
      }
    } catch {
      fail();
    } finally {
      decoding = false;
    }
  };
  socket.onmessage = (event: MessageEvent<Blob>) => {
    if (closed) return;
    latest = event.data;
    if (!decoding) void draw();
  };
  socket.onclose = fail;
  socket.onerror = fail;
  return close;
}
