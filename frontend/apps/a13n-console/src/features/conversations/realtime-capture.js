class RealtimeCapture extends AudioWorkletProcessor {
  position = 0;
  weight = 0;
  sum = 0;
  energy = 0;
  meterEnergy = 0;
  meterSamples = 0;
  meterTicks = 0;
  muted = false;
  voiced = 0;
  quiet = 0;
  speaking = false;

  constructor(options) {
    super();
    this.pcm = options.processorOptions.pcm;
    this.inputHz = options.processorOptions.inputHz;
    this.frame = new Int16Array(this.inputHz / 50);
    this.port.onmessage = ({ data }) => {
      if (typeof data?.muted !== "boolean") return;
      this.muted = data.muted;
      this.sum = this.weight = this.position = this.energy = 0;
      this.meterEnergy = this.meterSamples = this.meterTicks = 0;
      this.voiced = this.quiet = 0;
      this.speaking = false;
    };
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (input && !this.muted) {
      // Average input over each negotiated sample interval, carrying fractions across worklet blocks.
      for (const inputSample of input) {
        let remaining = this.inputHz;
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
            this.meterEnergy += this.energy;
            this.meterSamples += this.position;
            this.meterTicks += 15;
            if (this.meterTicks >= 50) {
              this.port.postMessage({
                level: Math.sqrt(this.meterEnergy / this.meterSamples),
              });
              this.meterTicks -= 50;
              this.meterEnergy = this.meterSamples = 0;
            }
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
              this.frame = new Int16Array(this.inputHz / 50);
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
