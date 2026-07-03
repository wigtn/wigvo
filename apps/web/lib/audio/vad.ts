/**
 * Client-side VAD (Voice Activity Detection).
 * RMS energy-based speech detection with onset/end delays.
 *
 * Thresholds adapt to the ambient noise floor: non-speech frames feed an
 * asymmetric EMA (fast decay, slow attack) and the effective thresholds are
 * `max(configured minimum, noiseFloor × ratio)`. In a quiet room this
 * degenerates to the fixed configured thresholds.
 *
 * State machine:
 *   SILENCE --(speech for speechOnsetDelay)--> SPEECH
 *   SPEECH --(silence for speechEndDelay)--> COMMITTED
 *   COMMITTED --(reset)--> SILENCE
 */

export type VadState = 'silence' | 'speech' | 'committed';

export interface VadConfig {
  /** Minimum RMS to enter speech (floor of the adaptive threshold). */
  speechThreshold: number;
  /** Minimum RMS to count as silence (floor of the adaptive threshold). */
  silenceThreshold: number;
  speechOnsetDelay: number;
  speechEndDelay: number;
  sampleRate: number;
  chunkSize: number;
  /** Starting noise-floor estimate (RMS). */
  noiseFloorInitial: number;
  /** Hard cap on the noise-floor estimate. */
  noiseFloorMax: number;
  /** EMA coefficient when RMS drops below the floor (fast adapt down). */
  noiseFloorDecay: number;
  /** EMA coefficient when RMS rises above the floor (slow adapt up). */
  noiseFloorAttack: number;
  /** Speech threshold = noiseFloor × this ratio (min: speechThreshold). */
  speechFloorRatio: number;
  /** Silence threshold = noiseFloor × this ratio (min: silenceThreshold). */
  silenceFloorRatio: number;
}

const DEFAULT_VAD_CONFIG: VadConfig = {
  speechThreshold: 0.015,
  silenceThreshold: 0.008,
  speechOnsetDelay: 150,
  speechEndDelay: 350,
  sampleRate: 16000,
  chunkSize: 1600,
  noiseFloorInitial: 0.004,
  noiseFloorMax: 0.04,
  noiseFloorDecay: 0.3,
  noiseFloorAttack: 0.05,
  speechFloorRatio: 3.0,
  silenceFloorRatio: 1.8,
};

export interface VadCallbacks {
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
  onCommitted?: () => void;
}

export class ClientVAD {
  private state: VadState = 'silence';
  private config: VadConfig;
  private callbacks: VadCallbacks;

  private speechStartTime = 0;
  private silenceStartTime = 0;
  private currentRms = 0;
  private noiseFloor: number;

  constructor(config?: Partial<VadConfig>, callbacks?: VadCallbacks) {
    this.config = { ...DEFAULT_VAD_CONFIG, ...config };
    this.callbacks = callbacks ?? {};
    this.noiseFloor = this.config.noiseFloorInitial;
  }

  getState(): VadState {
    return this.state;
  }

  getRms(): number {
    return this.currentRms;
  }

  getNoiseFloor(): number {
    return this.noiseFloor;
  }

  /** Effective speech-onset threshold (noise-floor adaptive). */
  getSpeechThreshold(): number {
    return Math.max(
      this.config.speechThreshold,
      this.noiseFloor * this.config.speechFloorRatio
    );
  }

  /** Effective silence threshold (noise-floor adaptive). */
  getSilenceThreshold(): number {
    return Math.max(
      this.config.silenceThreshold,
      this.noiseFloor * this.config.silenceFloorRatio
    );
  }

  /**
   * Process Float32 audio samples and update VAD state.
   * @returns current state after processing
   */
  processSamples(samples: Float32Array): VadState {
    const rms = this.calculateRms(samples);
    this.currentRms = rms;
    this.updateNoiseFloor(rms);
    const now = Date.now();

    switch (this.state) {
      case 'silence':
        if (rms >= this.getSpeechThreshold()) {
          if (this.speechStartTime === 0) {
            this.speechStartTime = now;
          }
          if (now - this.speechStartTime >= this.config.speechOnsetDelay) {
            this.transition('speech');
            this.silenceStartTime = 0;
          }
        } else {
          this.speechStartTime = 0;
        }
        break;

      case 'speech':
        if (rms < this.getSilenceThreshold()) {
          if (this.silenceStartTime === 0) {
            this.silenceStartTime = now;
          }
          if (now - this.silenceStartTime >= this.config.speechEndDelay) {
            this.transition('committed');
          }
        } else {
          this.silenceStartTime = 0;
        }
        break;

      case 'committed':
        // Stay until explicitly reset
        break;
    }

    return this.state;
  }

  /** Reset to silence state. Keeps the learned noise floor. */
  reset(): void {
    this.transition('silence');
    this.speechStartTime = 0;
    this.silenceStartTime = 0;
    this.currentRms = 0;
  }

  /**
   * Track the ambient noise floor from frames classified as non-speech.
   * Speech-level frames are excluded so talking doesn't inflate the floor;
   * decay is fast so the floor recovers quickly when noise stops.
   */
  private updateNoiseFloor(rms: number): void {
    if (rms >= this.getSpeechThreshold()) return;
    const coeff =
      rms < this.noiseFloor
        ? this.config.noiseFloorDecay
        : this.config.noiseFloorAttack;
    this.noiseFloor += (rms - this.noiseFloor) * coeff;
    this.noiseFloor = Math.min(this.noiseFloor, this.config.noiseFloorMax);
  }

  private calculateRms(samples: Float32Array): number {
    if (samples.length === 0) return 0;
    let sumSquares = 0;
    for (let i = 0; i < samples.length; i++) {
      sumSquares += samples[i] * samples[i];
    }
    return Math.sqrt(sumSquares / samples.length);
  }

  private transition(newState: VadState): void {
    if (newState === this.state) return;
    const prev = this.state;
    this.state = newState;

    if (newState === 'speech' && prev === 'silence') {
      this.callbacks.onSpeechStart?.();
    } else if (newState === 'committed' && prev === 'speech') {
      this.callbacks.onSpeechEnd?.();
      this.callbacks.onCommitted?.();
    }
  }
}
