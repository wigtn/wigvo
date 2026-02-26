'use client';

import { create } from 'zustand';
import type { CommunicationMode } from '@/shared/call-types';

// 대화 요약 (사이드바용)
export interface ConversationSummary {
  id: string;
  status: string;
  targetName: string | null;
  lastMessage: string;
  createdAt: string;
}

interface DashboardState {
  // 사이드바
  isSidebarOpen: boolean;
  isSidebarCollapsed: boolean;
  activeMenu: 'chat' | 'conversations';

  // 대화 목록
  conversations: ConversationSummary[];
  activeConversationId: string | null;

  // 시나리오 선택 상태 (전역 공유)
  scenarioSelected: boolean;

  // 통화 상태 (인라인)
  callingCallId: string | null;
  callingCommunicationMode: CommunicationMode | null;

  // Actions
  setSidebarOpen: (open: boolean) => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setActiveMenu: (menu: 'chat' | 'conversations') => void;
  setConversations: (conversations: ConversationSummary[]) => void;
  setActiveConversationId: (id: string | null) => void;
  setScenarioSelected: (selected: boolean) => void;
  setCallingCallId: (id: string | null) => void;
  setCallingCommunicationMode: (mode: CommunicationMode | null) => void;

  // 복합 액션
  resetDashboard: () => void;
  resetCalling: () => void;
}

export const useDashboard = create<DashboardState>((set) => ({
  // Initial state
  isSidebarOpen: true,
  isSidebarCollapsed: false,
  activeMenu: 'chat',
  conversations: [],
  activeConversationId: null,
  scenarioSelected: false,
  callingCallId: null,
  callingCommunicationMode: null,

  // Actions
  setSidebarOpen: (open) => set({ isSidebarOpen: open }),
  setSidebarCollapsed: (collapsed) => set({ isSidebarCollapsed: collapsed }),
  setActiveMenu: (menu) => set({ activeMenu: menu }),
  setConversations: (conversations) => set({ conversations }),
  setActiveConversationId: (id) => set({ activeConversationId: id }),
  setScenarioSelected: (selected) => set({ scenarioSelected: selected }),
  setCallingCallId: (id) => set({ callingCallId: id }),
  setCallingCommunicationMode: (mode) => set({ callingCommunicationMode: mode }),

  // 대시보드 초기화
  resetDashboard: () =>
    set({
      scenarioSelected: false,
    }),

  // 통화 상태 초기화
  resetCalling: () =>
    set({
      callingCallId: null,
      callingCommunicationMode: null,
    }),
}));
