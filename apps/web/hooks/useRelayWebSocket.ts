'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { WsMessageType, type RelayWsMessage } from '@/shared/call-types';
import { MOCK_WS_URL_PREFIX } from '@/lib/demo';
import { MockWebSocket } from '@/lib/demo/mock-ws';
import { createClient } from '@/lib/supabase/client';

type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

interface UseRelayWebSocketOptions {
  url: string | null;
  onMessage: (msg: RelayWsMessage) => void;
  autoConnect: boolean;
  protocols?: string[];
  refreshProtocols?: () => Promise<string[]>;
}

interface UseRelayWebSocketReturn {
  status: WsStatus;
  sendMessage: (msg: RelayWsMessage) => boolean;
  sendAudioChunk: (base64Audio: string) => boolean;
  sendVadState: (state: string) => boolean;
  sendText: (text: string) => boolean;
  sendTypingState: () => boolean;
  sendEndCall: () => boolean;
  disconnect: () => void;
}

const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 3000;
const JWT_WS_PROTOCOL = 'wigvo.jwt';

export function useRelayWebSocket({
  url,
  onMessage,
  autoConnect,
  protocols,
  refreshProtocols,
}: UseRelayWebSocketOptions): UseRelayWebSocketReturn {
  const [status, setStatus] = useState<WsStatus>('disconnected');

  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);

  const reconnectCountRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stableConnectionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const connectGenerationRef = useRef(0);
  const connectRef = useRef<(isReconnect?: boolean) => Promise<void>>(() => Promise.resolve());
  const protocolsRef = useRef(protocols);
  const refreshProtocolsRef = useRef(refreshProtocols);

  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    protocolsRef.current = protocols;
  }, [protocols]);

  useEffect(() => {
    refreshProtocolsRef.current = refreshProtocols;
  }, [refreshProtocols]);

  const cleanup = useCallback(() => {
    connectGenerationRef.current += 1;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (stableConnectionTimerRef.current) {
      clearTimeout(stableConnectionTimerRef.current);
      stableConnectionTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
        wsRef.current.close();
      }
      wsRef.current = null;
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (reconnectCountRef.current < MAX_RECONNECT_ATTEMPTS) {
      reconnectCountRef.current += 1;
      setStatus('connecting');
      reconnectTimerRef.current = setTimeout(() => {
        void connectRef.current(true);
      }, RECONNECT_DELAY_MS);
    } else {
      setStatus('error');
    }
  }, []);

  const connect = useCallback(async (isReconnect = false) => {
    if (!url) return;

    cleanup();
    const generation = connectGenerationRef.current;
    intentionalCloseRef.current = false;
    setStatus('connecting');

    // Demo mode: use MockWebSocket for mock:// URLs
    let ws: WebSocket;
    if (url.startsWith(MOCK_WS_URL_PREFIX)) {
      ws = new MockWebSocket(url) as unknown as WebSocket;
    } else {
      let offeredProtocols: string[] | undefined;
      try {
        if (isReconnect && refreshProtocolsRef.current) {
          // Pickup tokens are intentionally short-lived. Every reconnect gets a
          // fresh token instead of retrying the token from the first page load.
          offeredProtocols = await refreshProtocolsRef.current();
        } else if (protocolsRef.current) {
          offeredProtocols = protocolsRef.current;
        } else {
          const supabase = createClient();
          const {
            data: { session },
          } = await supabase.auth.getSession();
          offeredProtocols = session?.access_token
            ? [JWT_WS_PROTOCOL, session.access_token]
            : undefined;
        }
      } catch (credentialError) {
        if (generation !== connectGenerationRef.current) return;
        console.error('[RelayWS] Failed to refresh WebSocket credentials:', credentialError);
        scheduleReconnect();
        return;
      }
      if (generation !== connectGenerationRef.current) return;
      ws = new WebSocket(url, offeredProtocols);
    }
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus('connected');
      // An auth rejection can briefly fire `open` before `close`. Only reset
      // the retry budget after the connection has stayed alive for a moment.
      stableConnectionTimerRef.current = setTimeout(() => {
        reconnectCountRef.current = 0;
        stableConnectionTimerRef.current = null;
      }, 1000);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as RelayWsMessage;
        onMessageRef.current(msg);
      } catch {
        console.warn('[RelayWS] Failed to parse message:', event.data);
      }
    };

    ws.onerror = () => {
      console.error('[RelayWS] WebSocket error');
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (stableConnectionTimerRef.current) {
        clearTimeout(stableConnectionTimerRef.current);
        stableConnectionTimerRef.current = null;
      }

      if (intentionalCloseRef.current) {
        setStatus('disconnected');
        return;
      }

      scheduleReconnect();
    };
  }, [url, cleanup, scheduleReconnect]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    cleanup();
    setStatus('disconnected');
  }, [cleanup]);

  const sendMessage = useCallback((msg: RelayWsMessage): boolean => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      return true;
    }
    console.warn('[RelayWS] Cannot send, WebSocket not connected');
    return false;
  }, []);

  const sendAudioChunk = useCallback(
    (base64Audio: string): boolean => {
      return sendMessage({ type: WsMessageType.AUDIO_CHUNK, data: { audio: base64Audio } });
    },
    [sendMessage],
  );

  const sendVadState = useCallback(
    (state: string): boolean => {
      return sendMessage({ type: WsMessageType.VAD_STATE, data: { state } });
    },
    [sendMessage],
  );

  const sendText = useCallback(
    (text: string): boolean => {
      return sendMessage({ type: WsMessageType.TEXT_INPUT, data: { text } });
    },
    [sendMessage],
  );

  const sendTypingState = useCallback((): boolean => {
    return sendMessage({ type: WsMessageType.TYPING_STATE, data: {} });
  }, [sendMessage]);

  const sendEndCall = useCallback((): boolean => {
    return sendMessage({ type: WsMessageType.END_CALL, data: {} });
  }, [sendMessage]);

  // Auto-connect when url is set and autoConnect is true
  useEffect(() => {
    let autoConnectTimer: ReturnType<typeof setTimeout> | null = null;
    if (autoConnect && url) {
      autoConnectTimer = setTimeout(() => {
        void connectRef.current();
      }, 0);
    }

    return () => {
      if (autoConnectTimer) clearTimeout(autoConnectTimer);
      cleanup();
    };
  }, [autoConnect, url, cleanup]);

  return {
    status,
    sendMessage,
    sendAudioChunk,
    sendVadState,
    sendText,
    sendTypingState,
    sendEndCall,
    disconnect,
  };
}
