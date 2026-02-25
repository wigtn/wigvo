import { useCallback, useRef, useState } from "react";
import { Audio } from "expo-av";
import * as FileSystem from "expo-file-system/legacy";

const RECORDING_OPTIONS: Audio.RecordingOptions = {
  isMeteringEnabled: true,
  android: {
    extension: ".wav",
    outputFormat: Audio.AndroidOutputFormat.DEFAULT,
    audioEncoder: Audio.AndroidAudioEncoder.DEFAULT,
    sampleRate: 16000,
    numberOfChannels: 1,
    bitRate: 256000,
  },
  ios: {
    extension: ".wav",
    outputFormat: Audio.IOSOutputFormat.LINEARPCM,
    audioQuality: Audio.IOSAudioQuality.HIGH,
    sampleRate: 16000,
    numberOfChannels: 1,
    bitRate: 256000,
    linearPCMBitDepth: 16,
    linearPCMIsBigEndian: false,
    linearPCMIsFloat: false,
  },
  web: {
    mimeType: "audio/wav",
    bitsPerSecond: 256000,
  },
};

interface UseAudioRecorderOptions {
  /** Called for each audio chunk (base64 PCM16) */
  onChunk?: (audioBase64: string) => void;
  /** Called with metering level (-160 to 0 dB) */
  onMetering?: (db: number) => void;
  /** Chunk interval in ms (default: 256ms) */
  chunkIntervalMs?: number;
}

interface UseAudioRecorderReturn {
  isRecording: boolean;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<void>;
}

export function useAudioRecorder({
  onChunk,
  onMetering,
  chunkIntervalMs = 256,
}: UseAudioRecorderOptions = {}): UseAudioRecorderReturn {
  const [isRecording, setIsRecording] = useState(false);
  const recordingRef = useRef<Audio.Recording | null>(null);
  const chunkTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onChunkRef = useRef(onChunk);
  const onMeteringRef = useRef(onMetering);
  onChunkRef.current = onChunk;
  onMeteringRef.current = onMetering;

  const stopCurrentRecording = useCallback(async (): Promise<string | null> => {
    const recording = recordingRef.current;
    if (!recording) return null;

    try {
      const status = await recording.getStatusAsync();
      if (status.isRecording) {
        await recording.stopAndUnloadAsync();
      }
      const uri = recording.getURI();
      recordingRef.current = null;
      return uri;
    } catch {
      recordingRef.current = null;
      return null;
    }
  }, []);

  const readAndSendChunk = useCallback(async (uri: string) => {
    try {
      const base64 = await FileSystem.readAsStringAsync(uri, {
        encoding: FileSystem.EncodingType.Base64,
      });
      // Skip WAV header (44 bytes = ~60 base64 chars)
      // WAV header is 44 bytes, which encodes to ceil(44/3)*4 = 60 base64 chars
      const pcmBase64 = base64.substring(60);
      if (pcmBase64.length > 0) {
        onChunkRef.current?.(pcmBase64);
      }
      // Clean up temp file
      await FileSystem.deleteAsync(uri, { idempotent: true });
    } catch {
      // Ignore read errors
    }
  }, []);

  const startNewChunkRecording = useCallback(async () => {
    try {
      const { recording } = await Audio.Recording.createAsync(RECORDING_OPTIONS);
      recordingRef.current = recording;

      // Get metering updates
      recording.setOnRecordingStatusUpdate((status) => {
        if (status.metering !== undefined) {
          onMeteringRef.current?.(status.metering);
        }
      });
      recording.setProgressUpdateInterval(100);
    } catch {
      // Recording creation failed
    }
  }, []);

  const processChunk = useCallback(async () => {
    // Stop current recording, read data, start new one
    const uri = await stopCurrentRecording();
    if (uri) {
      await readAndSendChunk(uri);
    }
    // Start a new chunk recording
    await startNewChunkRecording();
  }, [stopCurrentRecording, readAndSendChunk, startNewChunkRecording]);

  const startRecording = useCallback(async () => {
    try {
      // Request permissions
      const permission = await Audio.requestPermissionsAsync();
      if (!permission.granted) {
        throw new Error("Microphone permission not granted");
      }

      // Configure audio mode
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });

      // Start first chunk
      await startNewChunkRecording();
      setIsRecording(true);

      // Set up chunk timer
      chunkTimerRef.current = setInterval(processChunk, chunkIntervalMs);
    } catch (error) {
      console.error("Failed to start recording:", error);
      setIsRecording(false);
    }
  }, [chunkIntervalMs, startNewChunkRecording, processChunk]);

  const stopRecording = useCallback(async () => {
    // Clear chunk timer
    if (chunkTimerRef.current) {
      clearInterval(chunkTimerRef.current);
      chunkTimerRef.current = null;
    }

    // Stop and process final chunk
    const uri = await stopCurrentRecording();
    if (uri) {
      await readAndSendChunk(uri);
    }

    // Reset audio mode
    try {
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: false,
      });
    } catch {
      // Ignore
    }

    setIsRecording(false);
  }, [stopCurrentRecording, readAndSendChunk]);

  return {
    isRecording,
    startRecording,
    stopRecording,
  };
}
