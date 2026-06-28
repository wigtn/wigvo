import { useState } from "react";
import {
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useAuth } from "../../lib/AuthContext";
import { RELAY_SERVER_URL } from "../../lib/constants";
import { CallStartResponse } from "../../lib/types";

export default function HomeScreen() {
  const { user, signOut } = useAuth();
  const router = useRouter();

  const [phoneNumber, setPhoneNumber] = useState("");
  const [sourceLanguage, setSourceLanguage] = useState("en");
  const [targetLanguage, setTargetLanguage] = useState("ko");
  const [vadMode, setVadMode] = useState<"client" | "push_to_talk">("client");
  const [loading, setLoading] = useState(false);

  const handleStartCall = async () => {
    if (!phoneNumber.trim()) {
      Alert.alert("Error", "Please enter a phone number.");
      return;
    }

    setLoading(true);
    try {
      const callId = `call_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

      const response = await fetch(`${RELAY_SERVER_URL}/relay/calls/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          call_id: callId,
          phone_number: phoneNumber.trim(),
          mode: "relay",
          source_language: sourceLanguage,
          target_language: targetLanguage,
          vad_mode: vadMode,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const data: CallStartResponse = await response.json();
      router.push({
        pathname: "/(main)/call",
        params: {
          callId: data.call_id,
          relayWsUrl: data.relay_ws_url,
          initialMode: vadMode === "client" ? "voice" : "text",
        },
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to start call";
      Alert.alert("Call Error", message);
    } finally {
      setLoading(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOut();
    } catch {
      Alert.alert("Error", "Failed to sign out.");
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.greeting}>
        Welcome, {user?.email?.split("@")[0] ?? "User"}
      </Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Start a Call</Text>

        <Text style={styles.label}>Phone Number</Text>
        <TextInput
          style={styles.input}
          placeholder="+1 415 555 1234"
          placeholderTextColor="#9CA3AF"
          value={phoneNumber}
          onChangeText={setPhoneNumber}
          keyboardType="phone-pad"
          accessibilityLabel="Phone number input"
        />

        <View style={styles.langRow}>
          <View style={styles.langCol}>
            <Text style={styles.label}>Your Language</Text>
            <View style={styles.langPicker}>
              <Pressable
                style={[styles.langBtn, sourceLanguage === "en" && styles.langBtnActive]}
                onPress={() => setSourceLanguage("en")}
              >
                <Text
                  style={[styles.langBtnText, sourceLanguage === "en" && styles.langBtnTextActive]}
                >
                  EN
                </Text>
              </Pressable>
              <Pressable
                style={[styles.langBtn, sourceLanguage === "ko" && styles.langBtnActive]}
                onPress={() => setSourceLanguage("ko")}
              >
                <Text
                  style={[styles.langBtnText, sourceLanguage === "ko" && styles.langBtnTextActive]}
                >
                  KO
                </Text>
              </Pressable>
            </View>
          </View>

          <View style={styles.langCol}>
            <Text style={styles.label}>Target Language</Text>
            <View style={styles.langPicker}>
              <Pressable
                style={[styles.langBtn, targetLanguage === "en" && styles.langBtnActive]}
                onPress={() => setTargetLanguage("en")}
              >
                <Text
                  style={[styles.langBtnText, targetLanguage === "en" && styles.langBtnTextActive]}
                >
                  EN
                </Text>
              </Pressable>
              <Pressable
                style={[styles.langBtn, targetLanguage === "ko" && styles.langBtnActive]}
                onPress={() => setTargetLanguage("ko")}
              >
                <Text
                  style={[styles.langBtnText, targetLanguage === "ko" && styles.langBtnTextActive]}
                >
                  KO
                </Text>
              </Pressable>
            </View>
          </View>
        </View>

        <Text style={styles.label}>Input Mode</Text>
        <View style={styles.langPicker}>
          <Pressable
            style={[styles.langBtn, vadMode === "client" && styles.langBtnActive]}
            onPress={() => setVadMode("client")}
          >
            <Text
              style={[styles.langBtnText, vadMode === "client" && styles.langBtnTextActive]}
            >
              Voice
            </Text>
          </Pressable>
          <Pressable
            style={[styles.langBtn, vadMode === "push_to_talk" && styles.langBtnActive]}
            onPress={() => setVadMode("push_to_talk")}
          >
            <Text
              style={[styles.langBtnText, vadMode === "push_to_talk" && styles.langBtnTextActive]}
            >
              Text
            </Text>
          </Pressable>
        </View>

        <View style={{ height: 12 }} />

        <Pressable
          style={[styles.callButton, loading && styles.buttonDisabled]}
          onPress={handleStartCall}
          disabled={loading}
          accessibilityRole="button"
          accessibilityLabel="Start call"
        >
          <Text style={styles.callButtonText}>
            {loading ? "Starting Call..." : "Start Call"}
          </Text>
        </Pressable>
      </View>

      <Pressable style={styles.signOutBtn} onPress={handleSignOut} accessibilityRole="button">
        <Text style={styles.signOutText}>Sign Out</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#F3F4F6",
  },
  content: {
    padding: 20,
  },
  greeting: {
    fontSize: 20,
    fontWeight: "600",
    color: "#111827",
    marginBottom: 20,
  },
  card: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 20,
    shadowColor: "#000",
    shadowOpacity: 0.05,
    shadowOffset: { width: 0, height: 2 },
    shadowRadius: 8,
    elevation: 2,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: "600",
    color: "#111827",
    marginBottom: 16,
  },
  label: {
    fontSize: 14,
    fontWeight: "500",
    color: "#374151",
    marginBottom: 6,
  },
  input: {
    borderWidth: 1,
    borderColor: "#D1D5DB",
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
    color: "#111827",
    marginBottom: 16,
  },
  langRow: {
    flexDirection: "row",
    gap: 12,
    marginBottom: 20,
  },
  langCol: {
    flex: 1,
  },
  langPicker: {
    flexDirection: "row",
    gap: 8,
  },
  langBtn: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#D1D5DB",
    alignItems: "center",
    minHeight: 48,
    justifyContent: "center",
  },
  langBtnActive: {
    backgroundColor: "#4F46E5",
    borderColor: "#4F46E5",
  },
  langBtnText: {
    fontSize: 14,
    fontWeight: "600",
    color: "#6B7280",
  },
  langBtnTextActive: {
    color: "#fff",
  },
  callButton: {
    backgroundColor: "#10B981",
    borderRadius: 8,
    paddingVertical: 16,
    alignItems: "center",
    minHeight: 52,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  callButtonText: {
    color: "#fff",
    fontSize: 16,
    fontWeight: "700",
  },
  signOutBtn: {
    marginTop: 24,
    alignItems: "center",
    padding: 12,
  },
  signOutText: {
    fontSize: 14,
    color: "#EF4444",
    fontWeight: "500",
  },
});
