// @vitest-environment node
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

import processorSource from "./realtime-capture.js?raw";

it.each([true, false])(
  "detects sustained local speech with PCM forwarding %s",
  (pcm) => {
    const postMessage = vi.fn();
    let processor!: { process(inputs: Float32Array[][]): boolean };
    runInNewContext(processorSource, {
      AudioWorkletProcessor: class {
        port = { postMessage };
      },
      registerProcessor: (
        _name: string,
        Processor: new (settings: object) => typeof processor,
      ) => {
        processor = new Processor({ processorOptions: { pcm } });
      },
    });
    const feed = (level: number, frames: number) => {
      for (let i = 0; i < frames; i++)
        processor.process([[new Float32Array(480).fill(level)]]);
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
    expect(postMessage.mock.calls.length - onsets()).toBe(pcm ? 23 : 0);
  },
);
