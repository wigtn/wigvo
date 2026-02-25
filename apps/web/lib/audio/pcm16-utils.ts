// =============================================================================
// PCM16 Audio Utilities
// =============================================================================
// PCM16 ↔ Float32 변환, Base64 인코딩/디코딩
// =============================================================================

export const SAMPLE_RATE = 16000;
export const PLAYBACK_SAMPLE_RATE = 24000; // OpenAI Realtime API pcm16 output is 24kHz
export const CHANNELS = 1;

/**
 * PCM16 (Int16) ArrayBuffer를 Float32Array로 변환합니다.
 * 범위: [-32768, 32767] → [-1.0, 1.0]
 */
export function pcm16ToFloat32(pcm16: ArrayBuffer): Float32Array {
  const int16 = new Int16Array(pcm16);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768;
  }
  return float32;
}

/**
 * Float32Array를 PCM16 (Int16) ArrayBuffer로 변환합니다.
 * 범위: [-1.0, 1.0] → [-32768, 32767]
 */
export function float32ToPcm16(float32: Float32Array): ArrayBuffer {
  const int16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    int16[i] = s < 0 ? s * 32768 : s * 32767;
  }
  return int16.buffer;
}

/**
 * Base64 문자열을 ArrayBuffer로 디코딩합니다.
 */
export function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * ArrayBuffer를 Base64 문자열로 인코딩합니다.
 */
export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}
