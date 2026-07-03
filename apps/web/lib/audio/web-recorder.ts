/**
 * Web Audio API microphone recorder.
 * Captures PCM16 audio chunks via AudioWorklet (ScriptProcessorNode fallback).
 * Output: base64-encoded PCM16 chunks (1600 samples = 100ms @ 16kHz).
 */

import { arrayBufferToBase64, float32ToPcm16, SAMPLE_RATE } from './pcm16-utils';

const CHUNK_SIZE = 1600; // 100ms @ 16kHz

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

    // Try AudioWorklet first, fall back to ScriptProcessorNode
    const workletAvailable = await this.trySetupWorklet();
    if (!workletAvailable) {
      this.setupScriptProcessor();
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

  private async trySetupWorklet(): Promise<boolean> {
    if (!this.audioContext || !this.sourceNode) return false;

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

      this.sourceNode.connect(this.workletNode);
      this.workletNode.connect(this.audioContext.destination);

      return true;
    } catch {
      return false;
    }
  }

  private setupScriptProcessor(): void {
    if (!this.audioContext || !this.sourceNode) return;

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

    this.sourceNode.connect(this.scriptProcessorNode);
    this.scriptProcessorNode.connect(this.audioContext.destination);
  }

  private emitChunk(float32Samples: Float32Array): void {
    if (!this.onChunkCallback) return;
    const pcm16Buffer = float32ToPcm16(float32Samples);
    const base64 = arrayBufferToBase64(pcm16Buffer);
    this.onChunkCallback(base64, float32Samples);
  }
}
