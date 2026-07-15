import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Pressable,
  StyleSheet,
  Text,
  Vibration,
  View,
} from "react-native";
import { LiveCaptionPanel, LiveCaptionPanelHandle } from "./LiveCaptionPanel";
import { PushToTalkInput } from "./PushToTalkInput";
import { VadIndicator } from "./VadIndicator";
import { ModeSelector } from "./ModeSelector";
import { CallStatusOverlay } from "./CallStatusOverlay";
import { FontScaleControl } from "./FontScaleControl";
import { useRealtimeCall } from "../../hooks/useRealtimeCall";
import type { CaptionData, InputMode } from "../../lib/types";
import { RELAY_SERVER_URL } from "../../lib/constants";
import { supabase } from "../../lib/supabase";

type WsStatus = "disconnected" | "connecting" | "connected" | "error";
type RecoveryStatus = "recovering" | "degraded" | "recovered" | null;

interface RealtimeCallViewProps {
  callId: string;
  onCallEnd: () => void;
  /** Initial font scale for captions (1.0 = normal) */
  fontScale?: number;
  /** Initial input mode */
  initialMode?: InputMode;
}

/**
 * Main call view composing LiveCaptionPanel + Voice/Text input.
 * Handles WebSocket connection, VAD, audio playback, and call lifecycle.
 *
 * Accessibility [M-7]:
 * - Font size adjustment via FontScaleControl
 * - Vibration feedback on recipient speech
 * - Minimum 48x48dp button sizes
 * - Recovery status overlay (PRD 5.3)
 */
export function RealtimeCallView({
  callId,
  onCallEnd,
  fontScale: initialFontScale = 1.0,
  initialMode = "voice",
}: RealtimeCallViewProps) {
  const captionRef = useRef<LiveCaptionPanelHandle>(null);
  const [callStatus, setCallStatus] = useState("Connecting...");
  const [fontScale, setFontScale] = useState(initialFontScale);
  const [recoveryStatus, setRecoveryStatus] = useState<RecoveryStatus>(null);
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const recoveryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const call = useRealtimeCall({
    callId,
    onCaption: useCallback((data: CaptionData) => {
      captionRef.current?.addCaption(data);
      // Vibrate on recipient speech for accessibility [M-7]
      if (data.speaker === "recipient" && data.isFinal) {
        Vibration.vibrate(100);
      }
    }, []),
    onCallStatus: useCallback((message: string) => {
      setCallStatus(message);
    }, []),
    onInterruptAlert: useCallback(() => {
      setCallStatus("Recipient is speaking...");
      Vibration.vibrate([0, 50, 50, 50]); // Double vibration for interrupt [M-7]
    }, []),
    onError: useCallback((message: string) => {
      Alert.alert("Error", message);
    }, []),
    onRecoveryStatus: useCallback(
      (status: string, message: string) => {
        setRecoveryStatus(status as RecoveryStatus);
        setRecoveryMessage(message);

        // Auto-hide "recovered" after 3 seconds
        if (status === "recovered") {
          if (recoveryTimerRef.current) {
            clearTimeout(recoveryTimerRef.current);
          }
          recoveryTimerRef.current = setTimeout(() => {
            setRecoveryStatus(null);
            setRecoveryMessage("");
          }, 3000);
        }
      },
      []
    ),
  });

  // Cleanup recovery timer
  useEffect(() => {
    return () => {
      if (recoveryTimerRef.current) {
        clearTimeout(recoveryTimerRef.current);
      }
    };
  }, []);

  const handleSendText = useCallback(
    (text: string) => {
      call.sendText(text);
      // Add user's text as a local caption immediately
      captionRef.current?.addCaption({
        text,
        speaker: "user",
        language: "input",
        isFinal: true,
      });
    },
    [call]
  );

  const handleEndCall = useCallback(async () => {
    call.endCall();
    call.disconnect();
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      await fetch(`${RELAY_SERVER_URL}/relay/calls/${callId}/end`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(session?.access_token
            ? { Authorization: `Bearer ${session.access_token}` }
            : {}),
        },
        body: JSON.stringify({ call_id: callId, reason: "user_hangup" }),
      });
    } catch {
      // Best-effort cleanup
    }
    onCallEnd();
  }, [callId, call, onCallEnd]);

  const statusColor: Record<WsStatus, string> = {
    connected: "#10B981",
    connecting: "#F59E0B",
    error: "#EF4444",
    disconnected: "#6B7280",
  };

  return (
    <View style={styles.container}>
      {/* Connection status */}
      <View style={styles.statusBar}>
        <View
          style={[
            styles.statusDot,
            { backgroundColor: statusColor[call.wsStatus] },
          ]}
        />
        <Text style={styles.statusText}>{callStatus}</Text>
      </View>

      {/* Recovery status overlay (PRD 5.3) */}
      <CallStatusOverlay
        recoveryStatus={recoveryStatus}
        recoveryMessage={recoveryMessage}
      />

      {/* Mode selector + Font scale control */}
      <View style={styles.controlsRow}>
        <ModeSelector
          mode={call.inputMode}
          onModeChange={call.setInputMode}
          disabled={call.wsStatus !== "connected"}
        />
        <FontScaleControl
          fontScale={fontScale}
          onFontScaleChange={setFontScale}
        />
      </View>

      {/* VAD indicator (voice mode only) */}
      {call.inputMode === "voice" && (
        <VadIndicator
          vadState={call.vadState}
          energyLevel={call.energyLevel}
          isRecording={call.isRecording}
        />
      )}

      {/* Translation state indicator */}
      {call.translationState === "processing" && (
        <View style={styles.translatingBar}>
          <Text style={styles.translatingText}>Translating...</Text>
        </View>
      )}

      {/* Captions */}
      <LiveCaptionPanel ref={captionRef} fontScale={fontScale} />

      {/* Input */}
      <PushToTalkInput
        onSendText={handleSendText}
        inputMode={call.inputMode}
        isRecording={call.isRecording}
        onToggleRecording={call.toggleRecording}
        disabled={call.wsStatus !== "connected"}
      />

      {/* End call */}
      <Pressable
        style={styles.endCallButton}
        onPress={handleEndCall}
        accessibilityRole="button"
        accessibilityLabel="End call"
        accessibilityHint="Ends the current call"
      >
        <Text style={styles.endCallText}>End Call</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#F9FAFB",
  },
  statusBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginRight: 8,
  },
  statusText: {
    fontSize: 14,
    color: "#374151",
  },
  controlsRow: {
    backgroundColor: "#fff",
  },
  translatingBar: {
    backgroundColor: "#FEF3C7",
    paddingVertical: 6,
    paddingHorizontal: 16,
    alignItems: "center",
  },
  translatingText: {
    fontSize: 13,
    color: "#92400E",
    fontWeight: "600",
  },
  endCallButton: {
    backgroundColor: "#EF4444",
    margin: 12,
    borderRadius: 8,
    paddingVertical: 16,
    alignItems: "center",
    minHeight: 52,
  },
  endCallText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "700",
  },
});
