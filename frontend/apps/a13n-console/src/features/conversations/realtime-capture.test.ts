// @vitest-environment node
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

import processorSource from "./realtime-capture.js?raw";

it.each([
  [true, 24000, 24000],
  [true, 44100, 24000],
  [true, 48000, 24000],
  [true, 48000, 16000],
  [false, 48000, 24000],
] as const)(
  "detects sustained local speech with PCM forwarding %s",
  (pcm, sampleRate, inputHz) => {
    const postMessage = vi.fn();
    let processor!: { process(inputs: Float32Array[][]): boolean };
    runInNewContext(processorSource, {
      sampleRate,
      AudioWorkletProcessor: class {
        port = { postMessage };
      },
      registerProcessor: (
        _name: string,
        Processor: new (settings: object) => typeof processor,
      ) => {
        processor = new Processor({ processorOptions: { pcm, inputHz } });
      },
    });
    const feed = (level: number, frames: number) => {
      const samples = (frames * sampleRate) / 50;
      for (let i = 0; i < samples; i += 128)
        processor.process([
          [new Float32Array(Math.min(128, samples - i)).fill(level)],
        ]);
    };
    const onsets = () =>
      postMessage.mock.calls.filter(([value]) => value === "speech").length;
    feed(0.005, 4);
    feed(0.2, 1);
    expect(onsets()).toBe(0);
    feed(0.2, 1);
    expect(onsets()).toBe(1);
    feed(0.2, 5);
    feed(0, 10);
    feed(0.2, 2);
    expect(onsets()).toBe(2);
    const frames = postMessage.mock.calls
      .map(([value]) => value)
      .filter((value) => value !== "speech");
    expect(frames).toHaveLength(pcm ? 23 : 0);
    expect(
      frames.every((frame) => frame.byteLength === (inputHz / 50) * 2),
    ).toBe(true);
  },
);
