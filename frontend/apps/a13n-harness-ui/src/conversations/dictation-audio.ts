import processor from "./dictation-worklet.js?url&no-inline";

function wav(chunks: ArrayBuffer[], rate: number) {
  const size = chunks.reduce((n, c) => n + c.byteLength, 0);
  const header = new DataView(new ArrayBuffer(44));
  const tag = (at: number, s: string) =>
    [...s].forEach((c, i) => header.setUint8(at + i, c.charCodeAt(0)));
  tag(0, "RIFF");
  header.setUint32(4, size + 36, true);
  tag(8, "WAVE");
  tag(12, "fmt ");
  header.setUint32(16, 16, true);
  header.setUint16(20, 1, true);
  header.setUint16(22, 1, true);
  header.setUint32(24, rate, true);
  header.setUint32(28, rate * 2, true);
  header.setUint16(32, 2, true);
  header.setUint16(34, 16, true);
  tag(36, "data");
  header.setUint32(40, size, true);
  return new Blob([header.buffer, ...chunks], { type: "audio/wav" });
}

export async function recordAudio(
  signal: AbortSignal,
  level: (value: number) => void,
  fail: (error: Error) => void,
) {
  if (!navigator.mediaDevices?.getUserMedia)
    throw new Error("Microphone unavailable");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1 },
  });
  const stopTracks = () => stream.getTracks().forEach((track) => track.stop());
  if (signal.aborted) {
    stopTracks();
    signal.throwIfAborted();
  }
  signal.addEventListener("abort", stopTracks, { once: true });
  try {
    const ctx = new AudioContext({ sampleRate: 16000 });
    const close = () => {
      stopTracks();
      if (ctx.state !== "closed") void ctx.close();
    };
    signal.addEventListener("abort", close, { once: true });
    try {
      await Promise.all([ctx.resume(), ctx.audioWorklet.addModule(processor)]);
      signal.throwIfAborted();
      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "dictation-pcm");
      const chunks: ArrayBuffer[] = [];
      let size = 0;
      let audible = false;
      let flushed: (() => void) | undefined;
      node.port.onmessage = ({ data }: { data: ArrayBuffer | null }) => {
        if (signal.aborted) return;
        if (data === null) {
          flushed?.();
          return;
        }
        size += data.byteLength;
        if (size > 10 * 1024 * 1024) {
          fail(new Error("Recording limit reached"));
          return;
        }
        chunks.push(data);
        const pcm = new Int16Array(data);
        let sum = 0;
        for (const sample of pcm) sum += (sample / 32768) ** 2;
        audible ||= sum > 0;
        level(Math.min(1, Math.sqrt(sum / pcm.length) * 6));
      };
      stream.getAudioTracks()[0].onended = () =>
        fail(new Error("Microphone disconnected"));
      source.connect(node);
      node.connect(ctx.destination);
      return async () => {
        await new Promise<void>((resolve, reject) => {
          flushed = resolve;
          signal.addEventListener("abort", () => reject(signal.reason), {
            once: true,
          });
          node.port.postMessage("stop");
        });
        source.disconnect();
        node.disconnect();
        close();
        signal.removeEventListener("abort", close);
        if (!audible) throw new Error("No sound recorded.");
        return wav(chunks, ctx.sampleRate);
      };
    } catch (e) {
      close();
      throw e;
    }
  } catch (e) {
    stopTracks();
    throw e;
  }
}

export function encodeAudio(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const file = new FileReader();
    file.onerror = () => reject(file.error);
    file.onload = () => resolve(String(file.result).split(",")[1]);
    file.readAsDataURL(blob);
  });
}
