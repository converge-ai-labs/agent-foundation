class RealtimeCapture extends AudioWorkletProcessor {
  frame = new Int16Array(480);
  position = 0;
  weight = 0;
  sum = 0;
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
      // Average input over each 24 kHz output interval, carrying fractions across worklet blocks.
      for (const inputSample of input) {
        let remaining = 24000;
        while (remaining > 0) {
          const weight = Math.min(remaining, sampleRate - this.weight);
          this.sum += inputSample * weight;
          this.weight += weight;
          remaining -= weight;
          if (this.weight < sampleRate) continue;
          const sample = this.sum / sampleRate;
          this.sum = this.weight = 0;
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
    }
    return true;
  }
}
registerProcessor("realtime-capture", RealtimeCapture);
