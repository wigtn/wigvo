// ============================================================================
// POST /api/calls/[id]/start
// ============================================================================
// Purpose: Relay Server를 통해 전화 발신 시작 (OpenAI Realtime API)
// 기존 ElevenLabs 로직은 주석 처리 (아래 참고)
// ============================================================================

import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { generateDynamicPrompt } from "@/lib/prompt-generator";
import { startRelayCall, formatPhoneToE164 } from "@/lib/relay-client";
import type { CallMode, CommunicationMode } from "@/shared/call-types";
import type { CollectedData } from "@/shared/types";

// --- Types for Supabase query result ---

interface CallWithConversation {
  id: string;
  conversation_id: string;
  user_id: string;
  target_phone: string;
  target_name: string | null;
  status: string;
  parsed_service: string | null;
  source_language: string | null;
  target_language: string | null;
  call_mode: CallMode | null;
  communication_mode: CommunicationMode | null;
  conversations: {
    collected_data: CollectedData;
    status: string;
  };
}

// --- POST Handler ---

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  // params는 Next.js 15+에서 Promise이므로 반드시 await
  const { id: callId } = await params;

  try {
    const supabase = await createClient();

    // ── 1. Auth check ──
    const {
      data: { user },
      error: authError,
    } = await supabase.auth.getUser();

    if (authError || !user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    // ── 2. Call 정보 조회 (conversation의 collected_data 포함) ──
    const { data: call, error: callError } = await supabase
      .from("calls")
      .select("*, conversations(collected_data, status)")
      .eq("id", callId)
      .eq("user_id", user.id)
      .single();

    if (callError || !call) {
      console.error("[Start] Call not found:", callError?.message);
      return NextResponse.json({ error: "Call not found" }, { status: 404 });
    }

    const typedCall = call as unknown as CallWithConversation;

    // ── 3. Call 상태 검증 ──
    if (typedCall.status !== "PENDING") {
      return NextResponse.json(
        { error: `Call is already in status: ${typedCall.status}` },
        { status: 400 },
      );
    }

    // ── 4. collected_data 추출 ──
    const collectedData: CollectedData = typedCall.conversations
      ?.collected_data || {
      target_name: typedCall.target_name,
      target_phone: typedCall.target_phone,
      scenario_type: null,
      scenario_sub_type: null,
      primary_datetime: null,
      service: typedCall.parsed_service,
      customer_name: null,
      party_size: null,
      fallback_datetimes: [],
      fallback_action: null,
      special_request: null,
      source_language: null,
      target_language: null,
    };

    // ── 5. Call 상태를 CALLING으로 업데이트 ──
    const { error: callingError } = await supabase
      .from("calls")
      .update({
        status: "CALLING",
        updated_at: new Date().toISOString(),
      })
      .eq("id", callId);

    if (callingError) {
      console.error(
        "[Start] Failed to update call status:",
        callingError.message,
      );
      return NextResponse.json(
        { error: "Failed to start call" },
        { status: 500 },
      );
    }

    // Conversation 상태도 CALLING으로 업데이트
    await supabase
      .from("conversations")
      .update({
        status: "CALLING",
        updated_at: new Date().toISOString(),
      })
      .eq("id", typedCall.conversation_id);

    // ── 6. 통화 모드 결정 + 프롬프트 ──
    const callMode: CallMode = typedCall.call_mode || "relay";
    const communicationMode: CommunicationMode =
      typedCall.communication_mode || "voice_to_voice";

    // Agent Mode일 때 system_prompt_override 생성
    let systemPromptOverride: string | undefined;
    if (callMode === "agent") {
      const { systemPrompt } = generateDynamicPrompt(collectedData);
      systemPromptOverride = systemPrompt;
    }

    // ── 7. 전화번호 E.164 포맷 변환 ──
    const phoneNumber = formatPhoneToE164(typedCall.target_phone);

    // ── 8. Relay Server에 통화 시작 요청 ──
    if (!collectedData.source_language || !collectedData.target_language) {
      console.warn("[Start] Language not set in collected_data, using defaults:", {
        source: collectedData.source_language,
        target: collectedData.target_language,
      });
    }

    let relayResult;
    try {
      relayResult = await startRelayCall({
        call_id: callId,
        phone_number: phoneNumber,
        mode: callMode,
        source_language: collectedData.source_language || typedCall.source_language || "ko",
        target_language: collectedData.target_language || typedCall.target_language || "en",
        vad_mode: callMode === "relay" ? "client" : "server",
        collected_data: collectedData as unknown as Record<string, unknown>,
        system_prompt_override: systemPromptOverride,
        communication_mode: communicationMode,
      });
    } catch (error) {
      console.error("[Start] Relay Server call failed:", error);

      await updateCallFailed(
        supabase,
        callId,
        typedCall.conversation_id,
        error instanceof Error
          ? error.message
          : "Relay Server call initiation failed",
      );

      return NextResponse.json(
        { error: "Failed to start call" },
        { status: 500 },
      );
    }

    // ── 9. DB 업데이트: relay_ws_url + IN_PROGRESS ──
    await supabase
      .from("calls")
      .update({
        status: "IN_PROGRESS",
        relay_ws_url: relayResult.relay_ws_url,
        call_mode: callMode,
        updated_at: new Date().toISOString(),
      })
      .eq("id", callId);

    // ── 10. 응답 반환 ──
    return NextResponse.json({
      success: true,
      callId,
      relayWsUrl: relayResult.relay_ws_url,
      callSid: relayResult.call_sid,
    });

    // =========================================================================
    // --- ElevenLabs 레거시 로직 (주석 처리) ---
    // =========================================================================
    //
    // const { systemPrompt, dynamicVariables } = generateDynamicPrompt(collectedData);
    // const phoneNumber = formatPhoneToE164(typedCall.target_phone);
    //
    // let callResponse;
    // try {
    //   callResponse = await startOutboundCall({
    //     phoneNumber,
    //     dynamicVariables,
    //     systemPrompt,
    //   });
    // } catch (error) {
    //   await updateCallFailed(supabase, callId, typedCall.conversation_id,
    //     error instanceof Error ? error.message : "ElevenLabs call initiation failed");
    //   return NextResponse.json({ error: "Failed to start call" }, { status: 500 });
    // }
    //
    // const elevenLabsConversationId = callResponse.conversation_id;
    //
    // await supabase.from("calls").update({
    //   elevenlabs_conversation_id: elevenLabsConversationId,
    //   status: "IN_PROGRESS",
    //   updated_at: new Date().toISOString(),
    // }).eq("id", callId);
    //
    // if (isMockMode()) {
    //   handleMockCompletion(supabase, callId, typedCall.conversation_id, collectedData);
    // } else {
    //   handleRealCompletion(supabase, callId, typedCall.conversation_id, elevenLabsConversationId);
    // }
    //
    // return NextResponse.json({ success: true, conversationId: elevenLabsConversationId });
    // =========================================================================
  } catch (error) {
    console.error("[Start] Unexpected error:", error);
    return NextResponse.json(
      { error: "Failed to start call" },
      { status: 500 },
    );
  }
}

// --- Helper: Call 실패 처리 ---

async function updateCallFailed(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  supabase: any,
  callId: string,
  conversationId: string,
  errorMessage: string,
) {
  try {
    await supabase
      .from("calls")
      .update({
        status: "FAILED",
        result: "ERROR",
        summary: errorMessage,
        completed_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      })
      .eq("id", callId);

    try {
      await supabase
        .from("conversations")
        .update({
          status: "COMPLETED",
          updated_at: new Date().toISOString(),
        })
        .eq("id", conversationId);
    } catch (convErr) {
      console.error(
        "[Helper] Conversation update failed (non-critical):",
        convErr,
      );
    }
  } catch (err) {
    console.error("[Helper] Failed to update call as FAILED:", err);
  }
}
