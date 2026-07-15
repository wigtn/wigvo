import { useCallback, useEffect, useRef, useState } from "react";
import { WsMessage, WsMessageType } from "../lib/types";
import { RELAY_WS_URL } from "../lib/constants";
import { supabase } from "../lib/supabase";

export type WsStatus = "disconnected" | "connecting" | "connected" | "error";

type MessageHandler = (msg: WsMessage) => void;

interface UseRelayWebSocketOptions {
  callId: string | null;
  onMessage?: MessageHandler;
  onStatusChange?: (status: WsStatus) => void;
  autoConnect?: boolean;
}

interface UseRelayWebSocketReturn {
  status: WsStatus;
  connect: () => void;
  disconnect: () => void;
  sendAudioChunk: (audioBase64: string) => boolean;
  sendTextInput: (text: string) => boolean;
  sendVadState: (state: string) => boolean;
  sendEndCall: () => boolean;
}

const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 3000;
const JWT_WS_PROTOCOL = "wigvo.jwt";

export function useRelayWebSocket({
  callId,
  onMessage,
  onStatusChange,
  autoConnect = false,
}: UseRelayWebSocketOptions): UseRelayWebSocketReturn {
  const [status, setStatus] = useState<WsStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  const onStatusChangeRef = useRef(onStatusChange);

  const reconnectCountRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const connectGenerationRef = useRef(0);
  const connectRef = useRef<() => Promise<void>>(() => Promise.resolve());

  // Keep refs in sync without mutating refs during render.
  useEffect(() => {
    onMessageRef.current = onMessage;
    onStatusChangeRef.current = onStatusChange;
  }, [onMessage, onStatusChange]);

  const updateStatus = useCallback((newStatus: WsStatus) => {
    setStatus(newStatus);
    onStatusChangeRef.current?.(newStatus);
  }, []);

  const cleanup = useCallback(() => {
    connectGenerationRef.current += 1;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.onmessage = null;
      if (
        wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING
      ) {
        wsRef.current.close();
      }
      wsRef.current = null;
    }
  }, []);

  const sendMessage = useCallback(
    (type: WsMessageType, data: Record<string, unknown> = {}): boolean => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type, data }));
        return true;
      }
      console.warn("[RelayWS] Cannot send, WebSocket not connected");
      return false;
    },
    []
  );

  const connect = useCallback(async () => {
    if (!callId) return;

    cleanup();
    const generation = connectGenerationRef.current;
    intentionalCloseRef.current = false;
    updateStatus("connecting");

    const url = `${RELAY_WS_URL}/relay/calls/${callId}/stream`;
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (generation !== connectGenerationRef.current) return;
    const protocols = session?.access_token
      ? [JWT_WS_PROTOCOL, session.access_token]
      : undefined;
    const ws = new WebSocket(url, protocols);
    wsRef.current = ws;

    ws.onopen = () => {
      updateStatus("connected");
      reconnectCountRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg: WsMessage = JSON.parse(event.data);
        onMessageRef.current?.(msg);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onerror = () => {
      console.error("[RelayWS] WebSocket error");
    };

    ws.onclose = () => {
      wsRef.current = null;

      if (intentionalCloseRef.current) {
        updateStatus("disconnected");
        return;
      }

      if (reconnectCountRef.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectCountRef.current += 1;
        updateStatus("connecting");
        reconnectTimerRef.current = setTimeout(() => {
          void connectRef.current();
        }, RECONNECT_DELAY_MS);
      } else {
        updateStatus("error");
      }
    };
  }, [callId, cleanup, updateStatus]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    cleanup();
    updateStatus("disconnected");
  }, [cleanup, updateStatus]);

  // Auto-connect when callId changes (if enabled)
  useEffect(() => {
    let autoConnectTimer: ReturnType<typeof setTimeout> | null = null;
    if (autoConnect && callId) {
      autoConnectTimer = setTimeout(() => {
        void connectRef.current();
      }, 0);
    }
    return () => {
      if (autoConnectTimer) clearTimeout(autoConnectTimer);
      cleanup();
    };
  }, [callId, autoConnect, cleanup]);

  const sendAudioChunk = useCallback(
    (audioBase64: string): boolean => {
      return sendMessage("audio_chunk", { audio: audioBase64 });
    },
    [sendMessage]
  );

  const sendTextInput = useCallback(
    (text: string): boolean => {
      return sendMessage("text_input", { text });
    },
    [sendMessage]
  );

  const sendVadState = useCallback(
    (state: string): boolean => {
      return sendMessage("vad_state", { state });
    },
    [sendMessage]
  );

  const sendEndCall = useCallback((): boolean => {
    return sendMessage("end_call");
  }, [sendMessage]);

  return {
    status,
    connect,
    disconnect,
    sendAudioChunk,
    sendTextInput,
    sendVadState,
    sendEndCall,
  };
}
