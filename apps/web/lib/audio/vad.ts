/**
 * Client-side VAD (Voice Activity Detection).
 * RMS energy-based speech detection with onset/end delays.
 *
 * State machine:
 *   SILENCE --(speech for speechOnsetDelay)--> SPEECH
 *   SPEECH --(silence for speechEndDelay)--> COMMITTED
 *   COMMITTED --(reset)--> SILENCE
 */

export type VadState = 'silence' | 'speech' | 'committed';

export interface VadConfig {
  speechThreshold: number;
  silenceThreshold: number;
  speechOnsetDelay: number;
  speechEndDelay: number;
  sampleRate: number;
  chunkSize: number;
}

const DEFAULT_VAD_CONFIG: VadConfig = {
  speechThreshold: 0.015,
  silenceThreshold: 0.008,
  speechOnsetDelay: 150,
  speechEndDelay: 350,
  sampleRate: 16000,
  chunkSize: 1600,
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

  constructor(config?: Partial<VadConfig>, callbacks?: VadCallbacks) {
    this.config = { ...DEFAULT_VAD_CONFIG, ...config };
    this.callbacks = callbacks ?? {};
  }

  getState(): VadState {
    return this.state;
  }

  getRms(): number {
    return this.currentRms;
  }

  /**
   * Process Float32 audio samples and update VAD state.
   * @returns current state after processing
   */
  processSamples(samples: Float32Array): VadState {
    const rms = this.calculateRms(samples);
    this.currentRms = rms;
    const now = Date.now();

    switch (this.state) {
      case 'silence':
        if (rms >= this.config.speechThreshold) {
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
        if (rms < this.config.silenceThreshold) {
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

  /** Reset to silence state. */
  reset(): void {
    this.transition('silence');
    this.speechStartTime = 0;
    this.silenceStartTime = 0;
    this.currentRms = 0;
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
