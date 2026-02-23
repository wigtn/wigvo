// =============================================================================
// Demo Mode — Mock WebSocket
// =============================================================================
// 실제 WebSocket 대신 타이머 기반으로 캡션 이벤트를 재생
// URL이 'mock://' 로 시작하면 이 클래스를 사용
// =============================================================================

import { DEMO_CAPTION_TIMELINE, type MockWsEvent } from './mock-data';

export const MOCK_WS_URL_PREFIX = 'mock://';

type MockWsReadyState = 0 | 1 | 2 | 3;

/**
 * MockWebSocket — WebSocket API를 흉내내는 가짜 구현체
 * useRelayWebSocket에서 new WebSocket(url) 대신 사용
 */
export class MockWebSocket {
  // WebSocket compatible properties
  readonly url: string;
  readyState: MockWsReadyState = 0; // CONNECTING

  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;

  private timers: ReturnType<typeof setTimeout>[] = [];
  private timeline: MockWsEvent[];

  // WebSocket static constants
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  // Instance constants (for compatibility)
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSING = 2;
  readonly CLOSED = 3;

  constructor(url: string) {
    this.url = url;
    this.timeline = [...DEMO_CAPTION_TIMELINE];

    // Simulate connection open after 100ms
    const openTimer = setTimeout(() => {
      this.readyState = 1; // OPEN
      if (this.onopen) {
        this.onopen(new Event('open'));
      }
      // Start playing the caption timeline
      this.startTimeline();
    }, 100);

    this.timers.push(openTimer);
  }

  private startTimeline(): void {
    for (const event of this.timeline) {
      const timer = setTimeout(() => {
        if (this.readyState !== 1) return; // Only emit if still open

        // Keep demo call in-progress until user explicitly ends it.
        if (
          event.type === 'call_status'
          && (event.data.status === 'ended' || event.data.status === 'completed' || event.data.status === 'failed')
        ) {
          return;
        }

        const message = {
          type: event.type,
          data: event.data,
        };

        if (this.onmessage) {
          this.onmessage(
            new MessageEvent('message', {
              data: JSON.stringify(message),
            }),
          );
        }
      }, event.delayMs);

      this.timers.push(timer);
    }
  }

  send(data: string): void {
    // Parse the message to handle end_call
    try {
      const msg = JSON.parse(data);
      if (msg.type === 'end_call') {
        // Immediately end the call
        this.clearTimers();
        if (this.onmessage) {
          this.onmessage(
            new MessageEvent('message', {
              data: JSON.stringify({
                type: 'call_status',
                data: { status: 'ended', message: 'Call ended by user' },
              }),
            }),
          );
        }
      }
      // Other messages (audio_chunk, vad_state, text_input) are silently ignored
    } catch {
      // ignore parse errors
    }
  }

  close(): void {
    this.clearTimers();
    this.readyState = 3; // CLOSED
    if (this.onclose) {
      this.onclose(new CloseEvent('close', { code: 1000, reason: 'Normal closure' }));
    }
  }

  private clearTimers(): void {
    for (const timer of this.timers) {
      clearTimeout(timer);
    }
    this.timers = [];
  }
}
