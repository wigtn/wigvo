'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import {
  createConversation,
  getConversation,
  sendChatMessage,
  createCall,
  startCall,
} from '@/lib/api';
import { validateMessage } from '@/lib/validation';
import type {
  Message,
  CollectedData,
  ConversationStatus,
  ScenarioType,
  ScenarioSubType,
} from '@/shared/types';
import type { CommunicationMode } from '@/shared/call-types';
import { getDefaultLanguagePairForLocale } from '@/shared/call-types';
import { createEmptyCollectedData } from '@/shared/types';
import { useDashboard } from '@/hooks/useDashboard';
import { getStoredLocale } from '@/lib/i18n';
import {
  STORAGE_KEY_CONVERSATION_ID,
  ERROR_AUTO_DISMISS_MS,
} from '@/lib/constants';

const STORAGE_KEY_COMMUNICATION_MODE = 'currentCommunicationMode';
const STORAGE_KEY_SOURCE_LANG = 'currentSourceLang';
const STORAGE_KEY_TARGET_LANG = 'currentTargetLang';

interface UseChatReturn {
  conversationId: string | null;
  messages: Message[];
  collectedData: CollectedData | null;
  isComplete: boolean;
  isLoading: boolean;
  isInitializing: boolean;
  conversationStatus: ConversationStatus;
  // v5: 모드 + 시나리오 선택 관련
  scenarioSelected: boolean;
  selectedScenario: ScenarioType | null;
  selectedSubType: ScenarioSubType | null;
  communicationMode: CommunicationMode | null;
  sourceLang: string;
  targetLang: string;
  handleScenarioSelect: (scenarioType: ScenarioType, subType: ScenarioSubType, communicationMode: CommunicationMode, sourceLang: string, targetLang: string) => Promise<void>;
  sendMessage: (content: string) => Promise<void>;
  handleConfirm: () => Promise<void>;
  handleEdit: () => void;
  handleNewConversation: () => Promise<void>;
  error: string | null;
}

export function useChat(): UseChatReturn {
  const router = useRouter();
  const t = useTranslations('chat');

  // ── Dashboard State ─────────────────────────────────────────
  const { scenarioSelected, setScenarioSelected, setCallingCallId, setCallingCommunicationMode, resetCalling, resetDashboard } = useDashboard();

  // ── State ───────────────────────────────────────────────────
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [collectedData, setCollectedData] = useState<CollectedData | null>(null);
  const [isComplete, setIsComplete] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isInitializing, setIsInitializing] = useState(true);
  const [conversationStatus, setConversationStatus] = useState<ConversationStatus>('COLLECTING');
  const [error, setError] = useState<string | null>(null);
  
  // v5: 모드 + 시나리오 선택 상태 (scenarioSelected는 useDashboard Zustand 스토어)
  const [selectedScenario, setSelectedScenario] = useState<ScenarioType | null>(null);
  const [selectedSubType, setSelectedSubType] = useState<ScenarioSubType | null>(null);
  const [communicationMode, setCommunicationMode] = useState<CommunicationMode | null>(null);
  const defaultPair = getDefaultLanguagePairForLocale(getStoredLocale());
  const [sourceLang, setSourceLang] = useState(defaultPair.source.code);
  const [targetLang, setTargetLang] = useState(defaultPair.target.code);

  // ── Refs (StrictMode 이중 초기화 방지) ─────────────────────
  const initializedRef = useRef(false);
  const errorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Helper: 에러 설정 (5초 후 자동 디스미스) ───────────────
  const setErrorWithAutoDismiss = useCallback((msg: string) => {
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    setError(msg);
    errorTimerRef.current = setTimeout(() => setError(null), ERROR_AUTO_DISMISS_MS);
  }, []);

  // ── Helper: 401 에러 처리 ──────────────────────────────────
  const handle401 = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY_CONVERSATION_ID);
    router.push('/login');
  }, [router]);

  // ── startConversation (v5: 모드 + 시나리오 타입 + 언어 지원) ───────
  const startConversation = useCallback(async (
    scenarioType?: ScenarioType,
    subType?: ScenarioSubType,
    mode?: CommunicationMode,
    srcLang?: string,
    tgtLang?: string
  ) => {
    try {
      const data = await createConversation(scenarioType, subType, mode, srcLang, tgtLang, getStoredLocale());
      setConversationId(data.id);
      setConversationStatus(data.status);
      setCollectedData(data.collectedData ?? createEmptyCollectedData());
      setIsComplete(false);

      // v5: 모드 + 시나리오 선택 상태 업데이트
      if (scenarioType && subType) {
        setScenarioSelected(true);
        setSelectedScenario(scenarioType);
        setSelectedSubType(subType);
      }
      if (mode) {
        setCommunicationMode(mode);
        localStorage.setItem(STORAGE_KEY_COMMUNICATION_MODE, mode);
      }
      if (srcLang) {
        setSourceLang(srcLang);
        localStorage.setItem(STORAGE_KEY_SOURCE_LANG, srcLang);
      }
      if (tgtLang) {
        setTargetLang(tgtLang);
        localStorage.setItem(STORAGE_KEY_TARGET_LANG, tgtLang);
      }

      // greeting 메시지 추가
      if (data.greeting) {
        setMessages([
          {
            id: `greeting-${data.id}`,
            role: 'assistant',
            content: data.greeting,
            createdAt: data.createdAt,
          },
        ]);
      }

      localStorage.setItem(STORAGE_KEY_CONVERSATION_ID, data.id);
    } catch (err) {
      if (err instanceof Error && err.message === 'Unauthorized') {
        handle401();
        return;
      }
      setErrorWithAutoDismiss(t('startError'));
    }
  }, [handle401, setErrorWithAutoDismiss, t]);

  // ── resumeConversation (v4: 시나리오 상태 복원) ────────────
  const resumeConversation = useCallback(
    async (id: string) => {
      try {
        const data = await getConversation(id);

        // 이미 완료된 대화면 새로 시작 (모드 선택 화면으로)
        if (data.status === 'COMPLETED' || data.status === 'CALLING') {
          localStorage.removeItem(STORAGE_KEY_CONVERSATION_ID);
          localStorage.removeItem(STORAGE_KEY_COMMUNICATION_MODE);
          localStorage.removeItem(STORAGE_KEY_SOURCE_LANG);
          localStorage.removeItem(STORAGE_KEY_TARGET_LANG);
          // v5: 모드 선택 화면으로 돌아감
          setScenarioSelected(false);
          setSelectedScenario(null);
          setSelectedSubType(null);
          setCommunicationMode(null);
          const resetPair = getDefaultLanguagePairForLocale(getStoredLocale());
          setSourceLang(resetPair.source.code);
          setTargetLang(resetPair.target.code);
          setIsInitializing(false);
          return;
        }

        setConversationId(data.id);
        setConversationStatus(data.status);
        setCollectedData(data.collectedData ?? createEmptyCollectedData());
        setIsComplete(data.status === 'READY');
        setMessages(data.messages ?? []);

        // v5: 모드 + 시나리오 + 언어 상태 복원
        const savedMode = localStorage.getItem(STORAGE_KEY_COMMUNICATION_MODE) as CommunicationMode | null;
        if (savedMode) {
          setCommunicationMode(savedMode);
        }
        const savedSourceLang = localStorage.getItem(STORAGE_KEY_SOURCE_LANG);
        const savedTargetLang = localStorage.getItem(STORAGE_KEY_TARGET_LANG);
        if (savedSourceLang) setSourceLang(savedSourceLang);
        if (savedTargetLang) setTargetLang(savedTargetLang);

        if (data.collectedData?.scenario_type && data.collectedData?.scenario_sub_type) {
          setScenarioSelected(true);
          setSelectedScenario(data.collectedData.scenario_type);
          setSelectedSubType(data.collectedData.scenario_sub_type);
        } else {
          setScenarioSelected(false);
          setSelectedScenario(null);
          setSelectedSubType(null);
        }
      } catch (err) {
        if (err instanceof Error && err.message === 'Unauthorized') {
          handle401();
          return;
        }
        // 404 또는 기타 에러: localStorage 삭제 후 모드 선택 화면으로
        localStorage.removeItem(STORAGE_KEY_CONVERSATION_ID);
        localStorage.removeItem(STORAGE_KEY_COMMUNICATION_MODE);
        localStorage.removeItem(STORAGE_KEY_SOURCE_LANG);
        localStorage.removeItem(STORAGE_KEY_TARGET_LANG);
        setScenarioSelected(false);
        setSelectedScenario(null);
        setSelectedSubType(null);
        setCommunicationMode(null);
        const resetPair2 = getDefaultLanguagePairForLocale(getStoredLocale());
        setSourceLang(resetPair2.source.code);
        setTargetLang(resetPair2.target.code);
      }
    },
    [handle401]
  );

  // ── 초기화 (v4: 시나리오 선택 화면부터 시작) ────────────────
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;

    const init = async () => {
      setIsInitializing(true);
      const savedId = localStorage.getItem(STORAGE_KEY_CONVERSATION_ID);

      if (savedId) {
        // 기존 대화 복원 시도
        await resumeConversation(savedId);
      } else {
        // v5: 새 대화는 모드 선택 화면부터 시작
        setScenarioSelected(false);
        setSelectedScenario(null);
        setSelectedSubType(null);
        setCommunicationMode(null);
      }

      setIsInitializing(false);
    };

    init();
  }, [resumeConversation]);
  
  // ── handleScenarioSelect (v5: 모드 + 시나리오 + 언어 선택 후 대화 시작) ───
  const handleScenarioSelect = useCallback(async (
    scenarioType: ScenarioType,
    subType: ScenarioSubType,
    mode: CommunicationMode,
    srcLang: string,
    tgtLang: string
  ) => {
    setIsLoading(true);
    setError(null);

    try {
      await startConversation(scenarioType, subType, mode, srcLang, tgtLang);
    } catch (err) {
      if (err instanceof Error && err.message === 'Unauthorized') {
        handle401();
        return;
      }
      setErrorWithAutoDismiss(t('startError'));
    } finally {
      setIsLoading(false);
    }
  }, [startConversation, handle401, setErrorWithAutoDismiss, t]);

  // ── sendMessage (Optimistic update + rollback) ─────────────
  const sendMessage = useCallback(
    async (content: string) => {
      // 유효성 검사
      const validation = validateMessage(content);
      if (!validation.valid) {
        setErrorWithAutoDismiss(validation.error ?? t('validationError'));
        return;
      }

      if (!conversationId) {
        setErrorWithAutoDismiss(t('conversationNotStarted'));
        return;
      }

      setError(null);

      // 1. Optimistic: 사용자 메시지 즉시 추가
      const optimisticMsg: Message = {
        id: `temp-${Date.now()}`,
        role: 'user',
        content: content.trim(),
        createdAt: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, optimisticMsg]);
      setIsLoading(true);

      try {
        // 2. API 호출
        const data = await sendChatMessage(
          conversationId,
          content.trim(),
          communicationMode || undefined,
          getStoredLocale()
        );

        // 3. 성공: assistant 메시지 추가 + collected 데이터 업데이트
        const assistantMsg: Message = {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: data.message,
          createdAt: new Date().toISOString(),
        };

        setMessages((prev) => [...prev, assistantMsg]);
        setCollectedData(data.collected);
        // 서버가 READY면 카드 표시 보장 (파싱 누락 시에도)
        const ready = data.is_complete || data.conversation_status === 'READY';
        setIsComplete(ready);
        setConversationStatus(data.conversation_status);
      } catch (err) {
        // 실패: rollback — optimistic 메시지 제거
        setMessages((prev) => prev.filter((m) => m.id !== optimisticMsg.id));

        if (err instanceof Error && err.message === 'Unauthorized') {
          handle401();
          return;
        }
        setErrorWithAutoDismiss(t('sendError'));
      } finally {
        setIsLoading(false);
      }
    },
    [conversationId, communicationMode, handle401, setErrorWithAutoDismiss, t]
  );

  // ── handleConfirm: 전화 걸기 (더블클릭 방지 포함) ─────────
  const confirmingRef = useRef(false);
  const handleConfirm = useCallback(async () => {
    if (!conversationId || confirmingRef.current) return;
    confirmingRef.current = true;

    setIsLoading(true);
    setError(null);

    try {
      // 1. Call 생성 (저장된 communicationMode 사용)
      const call = await createCall(conversationId, communicationMode || undefined);

      // 2. Call 시작
      await startCall(call.id);

      // 3. 인라인 calling 상태로 전환 (페이지 이동 없음)
      setCallingCallId(call.id);
      setCallingCommunicationMode(communicationMode || null);
    } catch (err) {
      if (err instanceof Error && err.message === 'Unauthorized') {
        handle401();
        return;
      }
      setErrorWithAutoDismiss(t('callError'));
    } finally {
      setIsLoading(false);
      confirmingRef.current = false;
    }
  }, [conversationId, communicationMode, handle401, setCallingCallId, setCallingCommunicationMode, setErrorWithAutoDismiss, t]);

  // ── handleEdit: 수정하기 ──────────────────────────────────
  const handleEdit = useCallback(() => {
    setIsComplete(false);
    setConversationStatus('COLLECTING');

    const editMsg: Message = {
      id: `system-edit-${Date.now()}`,
      role: 'assistant',
      content: t('editPrompt'),
      createdAt: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, editMsg]);
  }, [t]);

  // ── handleNewConversation: 새 대화 시작 (v5: 모드 선택 화면으로) ─
  const handleNewConversation = useCallback(async () => {
    localStorage.removeItem(STORAGE_KEY_CONVERSATION_ID);
    localStorage.removeItem(STORAGE_KEY_COMMUNICATION_MODE);
    localStorage.removeItem(STORAGE_KEY_SOURCE_LANG);
    localStorage.removeItem(STORAGE_KEY_TARGET_LANG);
    setMessages([]);
    setCollectedData(null);
    setIsComplete(false);
    setConversationStatus('COLLECTING');
    setConversationId(null);
    setError(null);
    // v5: 모드 선택 화면으로 돌아감
    setScenarioSelected(false);
    setSelectedScenario(null);
    setSelectedSubType(null);
    setCommunicationMode(null);
    const resetPair3 = getDefaultLanguagePairForLocale(getStoredLocale());
    setSourceLang(resetPair3.source.code);
    setTargetLang(resetPair3.target.code);
    // 대시보드 초기화 (지도, 검색결과, 통화)
    resetDashboard();
    resetCalling();
  }, [resetCalling]);

  return {
    conversationId,
    messages,
    collectedData,
    isComplete,
    isLoading,
    isInitializing,
    conversationStatus,
    // v5: 모드 + 시나리오 선택 관련
    scenarioSelected,
    selectedScenario,
    selectedSubType,
    communicationMode,
    sourceLang,
    targetLang,
    handleScenarioSelect,
    sendMessage,
    handleConfirm,
    handleEdit,
    handleNewConversation,
    error,
  };
}
