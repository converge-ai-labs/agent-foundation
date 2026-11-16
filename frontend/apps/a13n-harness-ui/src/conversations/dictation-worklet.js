// Capture mono PCM in batches; flush the final partial batch before Stop resolves.
class DictationPCM extends AudioWorkletProcessor {
  buffer = new Int16Array(1024);
  length = 0;
  stopped = false;
  constructor() {
    super();
    this.port.onmessage = () => {
      this.stopped = true;
      this.flush();
      this.port.postMessage(null);
    };
  }
  flush() {
    if (!this.length) return;
    const pcm = this.buffer.slice(0, this.length);
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    this.length = 0;
  }
  process(inputs) {
    if (this.stopped) return false;
    for (const sample of inputs[0]?.[0] ?? []) {
      this.buffer[this.length++] = Math.max(
        -32768,
        Math.min(32767, sample * 32768),
      );
      if (this.length === this.buffer.length) this.flush();
    }
    return true;
  }
}
registerProcessor("dictation-pcm", DictationPCM);
