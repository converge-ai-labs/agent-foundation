class RealtimeCapture extends AudioWorkletProcessor {
  frame = new Int16Array(480);
  position = 0;

  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) {
      for (const sample of input) {
        this.frame[this.position++] = Math.round(
          Math.max(-1, Math.min(1, sample)) * 32767,
        );
        if (this.position === this.frame.length) {
          this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
          this.frame = new Int16Array(480);
          this.position = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("realtime-capture", RealtimeCapture);
