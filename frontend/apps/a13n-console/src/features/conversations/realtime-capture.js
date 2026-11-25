class RealtimeCapture extends AudioWorkletProcessor {
  frame = new Int16Array(480);
  position = 0;
  energy = 0;
  voiced = 0;
  quiet = 0;
  speaking = false;

  constructor(options) {
    super();
    this.pcm = options.processorOptions.pcm;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) {
      for (const sample of input) {
        this.energy += sample * sample;
        this.frame[this.position++] = Math.round(
          Math.max(-1, Math.min(1, sample)) * 32767,
        );
        if (this.position === this.frame.length) {
          // Two voiced 20ms frames stop playback locally. Rearm after 200ms of quiet.
          if (this.energy / this.position >= 0.001) {
            this.quiet = 0;
            if (++this.voiced >= 2 && !this.speaking) {
              this.speaking = true;
              this.port.postMessage("speech");
            }
          } else {
            this.voiced = 0;
            if (++this.quiet >= 10) this.speaking = false;
          }
          if (this.pcm) {
            this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
            this.frame = new Int16Array(480);
          }
          this.energy = 0;
          this.position = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("realtime-capture", RealtimeCapture);
