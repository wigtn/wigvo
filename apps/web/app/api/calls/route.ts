// =============================================================================
// POST /api/calls - Call 생성
// GET /api/calls - Call 목록 조회
// =============================================================================
// BE1 소유 - 전화 기록 생성 및 조회
// API Contract: Endpoint 1, 2
// =============================================================================

import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import {
  getConversationById,
  updateConversationStatus,
} from '@/lib/supabase/chat';
import { CreateCallRequest, CollectedData, CallRow } from '@/shared/types';
import { communicationModeToCallMode } from '@/shared/call-types';
import type { CommunicationMode } from '@/shared/call-types';
import { toCallResponse } from '@/lib/supabase/helpers';

// -----------------------------------------------------------------------------
// POST /api/calls
// -----------------------------------------------------------------------------
export async function POST(request: NextRequest) {
  try {
    // 1. 인증 확인
    const supabase = await createClient();
    const {
      data: { user },
      error: authError,
    } = await supabase.auth.getUser();

    if (authError || !user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    // 2. 요청 파싱
    const body = (await request.json()) as CreateCallRequest;
    const { conversationId, communicationMode } = body;

    if (!conversationId) {
      return NextResponse.json(
        { error: 'conversationId is required' },
        { status: 400 }
      );
    }

    // 3. 대화 세션 조회
    const conversation = await getConversationById(conversationId);

    if (!conversation) {
      return NextResponse.json(
        { error: 'Conversation not found' },
        { status: 404 }
      );
    }

    // 4. 본인 대화인지 확인
    if (conversation.user_id !== user.id) {
      return NextResponse.json(
        { error: 'Conversation not found' },
        { status: 404 }
      );
    }

    // 5. 상태 검증
    switch (conversation.status) {
      case 'COLLECTING':
        return NextResponse.json(
          { error: 'Conversation is not ready for call' },
          { status: 400 }
        );
      case 'CALLING':
        return NextResponse.json(
          { error: 'Call already in progress' },
          { status: 400 }
        );
      case 'COMPLETED':
        return NextResponse.json(
          { error: 'Conversation already completed' },
          { status: 400 }
        );
      case 'CANCELLED':
        return NextResponse.json(
          { error: 'Conversation was cancelled' },
          { status: 400 }
        );
      case 'READY':
        // 정상 진행
        break;
      default:
        return NextResponse.json(
          { error: 'Invalid conversation status' },
          { status: 400 }
        );
    }

    // 6. collected_data에서 call 정보 추출
    const collectedData = conversation.collected_data as CollectedData;

    if (!collectedData.target_phone) {
      return NextResponse.json(
        { error: 'Target phone number is required' },
        { status: 400 }
      );
    }

    // 7. Call 레코드 생성
    const selectedMode: CommunicationMode = communicationMode || 'voice_to_voice';
    const callMode = communicationModeToCallMode(selectedMode);

    const { data: call, error: callError } = await supabase
      .from('calls')
      .insert({
        user_id: user.id,
        conversation_id: conversationId,
        request_type: collectedData.scenario_type || 'RESERVATION',
        target_name: collectedData.target_name,
        target_phone: collectedData.target_phone,
        parsed_date: collectedData.primary_datetime?.split(' ')[0] || null,
        parsed_time: collectedData.primary_datetime?.split(' ')[1] || null,
        parsed_service: collectedData.service,
        source_language: collectedData.source_language || null,
        target_language: collectedData.target_language || null,
        status: 'PENDING',
        call_mode: callMode,
        communication_mode: selectedMode,
      })
      .select()
      .single();

    if (callError || !call) {
      console.error('Failed to create call:', callError);
      return NextResponse.json(
        { error: 'Failed to create call' },
        { status: 500 }
      );
    }

    // 8. Conversation 상태를 CALLING으로 업데이트
    await updateConversationStatus(conversationId, 'CALLING');

    // 9. 응답
    return NextResponse.json(toCallResponse(call as CallRow), { status: 201 });
  } catch (error) {
    console.error('Failed to create call:', error);
    return NextResponse.json(
      { error: 'Failed to create call' },
      { status: 500 }
    );
  }
}

// -----------------------------------------------------------------------------
// GET /api/calls
// -----------------------------------------------------------------------------
export async function GET() {
  try {
    // 1. 인증 확인
    const supabase = await createClient();
    const {
      data: { user },
      error: authError,
    } = await supabase.auth.getUser();

    if (authError || !user) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    // 2. 본인의 calls 조회 (최신순, limit 20)
    const { data: calls, error } = await supabase
      .from('calls')
      .select('*')
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .limit(20);

    if (error) {
      console.error('Failed to get calls:', error);
      return NextResponse.json(
        { error: 'Failed to get calls' },
        { status: 500 }
      );
    }

    // 3. 응답
    return NextResponse.json({
      calls: (calls || []).map((call) => toCallResponse(call as CallRow)),
    });
  } catch (error) {
    console.error('Failed to get calls:', error);
    return NextResponse.json(
      { error: 'Failed to get calls' },
      { status: 500 }
    );
  }
}
