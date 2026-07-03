/**
 * Web Audio API microphone recorder.
 * Captures PCM16 audio chunks via AudioWorklet (ScriptProcessorNode fallback).
 * Noise reduction: high-pass filter + Speex denoiser (WASM AudioWorklet),
 * on top of the browser's built-in noiseSuppression/echoCancellation.
 * Output: base64-encoded PCM16 chunks (1600 samples = 100ms @ 16kHz).
 */

import type { SpeexWorkletNode } from '@sapphi-red/web-noise-suppressor';
import { arrayBufferToBase64, float32ToPcm16, SAMPLE_RATE } from './pcm16-utils';

const CHUNK_SIZE = 1600; // 100ms @ 16kHz

// Speex denoiser assets copied from @sapphi-red/web-noise-suppressor dist
// into public/ (AudioWorklet modules and wasm must be fetched by URL).
const SPEEX_WORKLET_URL = '/noise-suppressor/speexWorklet.js';
const SPEEX_WASM_URL = '/noise-suppressor/speex.wasm';

// High-pass cutoff: removes low-frequency rumble/hum below the voice band.
const HIGHPASS_FREQUENCY_HZ = 100;

interface SpeexAssets {
  SpeexWorkletNode: typeof SpeexWorkletNode;
  wasmBinary: ArrayBuffer;
}

// Loaded once per page; recorder instances are recreated per call.
// The package is imported dynamically because it touches browser-only
// globals (AudioWorkletNode) at module scope, which breaks SSR.
let speexAssetsPromise: Promise<SpeexAssets> | null = null;

function loadSpeexAssets(): Promise<SpeexAssets> {
  if (!speexAssetsPromise) {
    speexAssetsPromise = (async () => {
      const mod = await import('@sapphi-red/web-noise-suppressor');
      const wasmBinary = await mod.loadSpeex({ url: SPEEX_WASM_URL });
      return { SpeexWorkletNode: mod.SpeexWorkletNode, wasmBinary };
    })().catch((err) => {
      speexAssetsPromise = null;
      throw err;
    });
  }
  return speexAssetsPromise;
}

export type ChunkCallback = (base64Audio: string, float32Samples: Float32Array) => void;

/**
 * AudioWorklet processor source code (inlined to avoid separate file hosting).
 * Collects samples and posts them to the main thread in CHUNK_SIZE batches.
 */
const WORKLET_PROCESSOR_CODE = `
class Pcm16CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(${CHUNK_SIZE});
    this.writeIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channelData = input[0];
    let readIndex = 0;

    while (readIndex < channelData.length) {
      const remaining = ${CHUNK_SIZE} - this.writeIndex;
      const available = channelData.length - readIndex;
      const toCopy = Math.min(remaining, available);

      this.buffer.set(
        channelData.subarray(readIndex, readIndex + toCopy),
        this.writeIndex
      );
      this.writeIndex += toCopy;
      readIndex += toCopy;

      if (this.writeIndex >= ${CHUNK_SIZE}) {
        this.port.postMessage({ samples: this.buffer.slice() });
        this.writeIndex = 0;
      }
    }

    return true;
  }
}

registerProcessor('pcm16-capture-processor', Pcm16CaptureProcessor);
`;

export class WebAudioRecorder {
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private highpassNode: BiquadFilterNode | null = null;
  private speexNode: SpeexWorkletNode | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private scriptProcessorNode: ScriptProcessorNode | null = null;
  private isActive = false;
  private onChunkCallback: ChunkCallback | null = null;

  // ScriptProcessorNode fallback buffer
  private spnBuffer: Float32Array = new Float32Array(CHUNK_SIZE);
  private spnWriteIndex = 0;

  /** Register a callback for audio chunks. */
  onChunk(callback: ChunkCallback): void {
    this.onChunkCallback = callback;
  }

  /** Start recording from the microphone. */
  async start(): Promise<void> {
    if (this.isActive) return;

    // Request microphone access
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    // Create AudioContext with target sample rate
    this.audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });

    // Safari: resume on user gesture context
    if (this.audioContext.state === 'suspended') {
      await this.audioContext.resume();
    }

    this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);

    // Noise reduction chain: source → high-pass → Speex denoiser → capture.
    // The Speex stage is best-effort; if its assets fail to load we fall back
    // to the browser's built-in noiseSuppression only.
    this.highpassNode = this.audioContext.createBiquadFilter();
    this.highpassNode.type = 'highpass';
    this.highpassNode.frequency.value = HIGHPASS_FREQUENCY_HZ;
    this.sourceNode.connect(this.highpassNode);

    let captureInput: AudioNode = this.highpassNode;
    try {
      const speex = await loadSpeexAssets();
      await this.audioContext.audioWorklet.addModule(SPEEX_WORKLET_URL);
      this.speexNode = new speex.SpeexWorkletNode(this.audioContext, {
        wasmBinary: speex.wasmBinary,
        maxChannels: 1,
      });
      this.highpassNode.connect(this.speexNode);
      captureInput = this.speexNode;
    } catch {
      this.speexNode = null;
    }

    // Try AudioWorklet first, fall back to ScriptProcessorNode
    const workletAvailable = await this.trySetupWorklet(captureInput);
    if (!workletAvailable) {
      this.setupScriptProcessor(captureInput);
    }

    this.isActive = true;
  }

  /** Stop recording and release resources. */
  stop(): void {
    if (!this.isActive) return;
    this.isActive = false;

    if (this.workletNode) {
      this.workletNode.disconnect();
      this.workletNode = null;
    }

    if (this.scriptProcessorNode) {
      this.scriptProcessorNode.disconnect();
      this.scriptProcessorNode = null;
    }

    if (this.speexNode) {
      this.speexNode.disconnect();
      this.speexNode.destroy();
      this.speexNode = null;
    }

    if (this.highpassNode) {
      this.highpassNode.disconnect();
      this.highpassNode = null;
    }

    if (this.sourceNode) {
      this.sourceNode.disconnect();
      this.sourceNode = null;
    }

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }

    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }

    this.spnWriteIndex = 0;
  }

  /** Whether the recorder is currently active. */
  get recording(): boolean {
    return this.isActive;
  }

  private async trySetupWorklet(captureInput: AudioNode): Promise<boolean> {
    if (!this.audioContext) return false;

    try {
      const blob = new Blob([WORKLET_PROCESSOR_CODE], { type: 'application/javascript' });
      const url = URL.createObjectURL(blob);

      await this.audioContext.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);

      this.workletNode = new AudioWorkletNode(this.audioContext, 'pcm16-capture-processor');
      this.workletNode.port.onmessage = (event: MessageEvent) => {
        const { samples } = event.data as { samples: Float32Array };
        this.emitChunk(samples);
      };

      captureInput.connect(this.workletNode);
      this.workletNode.connect(this.audioContext.destination);

      return true;
    } catch {
      return false;
    }
  }

  private setupScriptProcessor(captureInput: AudioNode): void {
    if (!this.audioContext) return;

    // Buffer size 4096 is widely supported
    this.scriptProcessorNode = this.audioContext.createScriptProcessor(4096, 1, 1);
    this.spnWriteIndex = 0;

    this.scriptProcessorNode.onaudioprocess = (event: AudioProcessingEvent) => {
      const inputData = event.inputBuffer.getChannelData(0);
      let readIndex = 0;

      while (readIndex < inputData.length) {
        const remaining = CHUNK_SIZE - this.spnWriteIndex;
        const available = inputData.length - readIndex;
        const toCopy = Math.min(remaining, available);

        this.spnBuffer.set(
          inputData.subarray(readIndex, readIndex + toCopy),
          this.spnWriteIndex
        );
        this.spnWriteIndex += toCopy;
        readIndex += toCopy;

        if (this.spnWriteIndex >= CHUNK_SIZE) {
          this.emitChunk(this.spnBuffer.slice());
          this.spnWriteIndex = 0;
        }
      }

      // Pass-through silence to keep the processor alive
      const outputData = event.outputBuffer.getChannelData(0);
      outputData.fill(0);
    };

    captureInput.connect(this.scriptProcessorNode);
    this.scriptProcessorNode.connect(this.audioContext.destination);
  }

  private emitChunk(float32Samples: Float32Array): void {
    if (!this.onChunkCallback) return;
    const pcm16Buffer = float32ToPcm16(float32Samples);
    const base64 = arrayBufferToBase64(pcm16Buffer);
    this.onChunkCallback(base64, float32Samples);
  }
}
