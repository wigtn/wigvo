/**
 * Client-side VAD configuration (PRD 4.2).
 * RMS-based energy detection parameters.
 */

export const VAD_CONFIG = {
  /** RMS threshold to detect speech start */
  speechThreshold: 0.015,
  /** RMS threshold to detect silence */
  silenceThreshold: 0.008,
  /** Delay before confirming speech onset (ms) */
  speechOnsetDelay: 150,
  /** Delay before confirming speech end (ms) */
  speechEndDelay: 350,
  /** Pre-speech buffer duration (ms) */
  preBufferDuration: 300,
  /** Audio sample rate (Hz) */
  sampleRate: 16000,
  /** Chunk size in samples (100ms @ 16kHz) */
  chunkSize: 1600,
  /** Chunk duration in ms */
  chunkDurationMs: 100,
} as const;

export type VadConfig = typeof VAD_CONFIG;
