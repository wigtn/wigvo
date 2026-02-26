"use client";

import { useCallback, useState, useEffect } from "react";
import { useTranslations } from "next-intl";
import { Menu, MessageSquare, Phone } from "lucide-react";
import Sidebar from "./Sidebar";
import MobileDrawer from "./MobileDrawer";
import ChatContainer from "@/components/chat/ChatContainer";
import RelayCallProvider from "@/components/call/RelayCallProvider";
import CallEffectPanel from "@/components/call/CallEffectPanel";
import ConversationHistoryPanel from "@/components/chat/ConversationHistoryPanel";

import { useDashboard } from "@/hooks/useDashboard";
import { cn } from "@/lib/utils";

export default function DashboardLayout() {
  const {
    activeMenu,
    scenarioSelected,
    setActiveConversationId,
    setSidebarOpen,
    resetDashboard,
    callingCallId,
    callingCommunicationMode,
  } = useDashboard();

  const t = useTranslations("dashboard");

  const [mobileTab, setMobileTab] = useState<"chat" | "calling">(
    "chat",
  );
  const isCalling = !!callingCallId;
  const mobileHeaderClass =
    "lg:hidden flex items-center px-4 py-2.5 glass-surface border-b border-white/70";
  const panelClass =
    "h-full dashboard-panel lg:rounded-3xl overflow-hidden";

  // calling 시작 시 모바일에서 자동 탭 전환
  useEffect(() => {
    if (isCalling) {
      const id = window.setTimeout(() => setMobileTab("calling"), 0);
      return () => window.clearTimeout(id);
    }
  }, [isCalling]);

  const onNewConversation = useCallback(() => {
    localStorage.removeItem('currentConversationId');
    localStorage.removeItem('currentCommunicationMode');
    localStorage.removeItem('currentSourceLang');
    localStorage.removeItem('currentTargetLang');
    resetDashboard();
    window.location.href = '/';
  }, [resetDashboard]);

  const onSelectConversation = useCallback(
    (id: string) => {
      setActiveConversationId(id);
    },
    [setActiveConversationId],
  );

  return (
    <div className="relative isolate flex h-full dashboard-shell overflow-hidden">
      {/* 데스크톱 사이드바 */}
      <div className="relative z-10 hidden lg:block">
        <Sidebar
          onNewConversation={onNewConversation}
          onSelectConversation={onSelectConversation}
        />
      </div>

      {/* 모바일 드로어 */}
      <MobileDrawer
        onNewConversation={onNewConversation}
        onSelectConversation={onSelectConversation}
      />

      {/* 메인 콘텐츠 */}
      {activeMenu === "conversations" ? (
        /* 대화 기록 전체 영역 */
        <div className="relative z-10 flex-1 flex flex-col overflow-hidden p-0 lg:p-4">
          {/* 모바일 헤더 (메뉴 버튼만) */}
          <div className={mobileHeaderClass}>
            <button
              onClick={() => setSidebarOpen(true)}
              className="p-2 hover:bg-white/55 rounded-lg transition-colors"
            >
              <Menu className="size-5 text-[#4A5D76]" />
            </button>
          </div>

          {/* 대화 기록 패널 */}
          <div className="flex-1 overflow-hidden p-0 lg:p-5">
            <div className={panelClass}>
              <ConversationHistoryPanel />
            </div>
          </div>
        </div>
      ) : (
        /* 채팅 + 통화 2-column 레이아웃 */
        <div className="relative z-10 flex-1 flex flex-col lg:flex-row gap-0 lg:gap-5 p-0 lg:p-8 overflow-hidden">
          {/* 모바일 헤더 (시나리오 선택 전: 메뉴만 / 선택 후: 메뉴+탭 전환) */}
          <div className={cn(
            mobileHeaderClass,
            scenarioSelected ? "justify-between" : "justify-start",
          )}>
            <button
              onClick={() => setSidebarOpen(true)}
              className="p-2 hover:bg-white/55 rounded-lg transition-colors"
            >
              <Menu className="size-5 text-[#4A5D76]" />
            </button>

            {scenarioSelected && isCalling && (
              <div className="flex bg-white/45 rounded-xl p-1 border border-white/70">
                <button
                  onClick={() => setMobileTab("chat")}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all",
                    mobileTab === "chat"
                      ? "bg-[#0B1324] text-white shadow-[0_4px_12px_rgba(8,23,55,0.3)]"
                      : "text-[#6B7E95]",
                  )}
                >
                  <MessageSquare className="size-4" />
                  {t("tabChat")}
                </button>
                <button
                  onClick={() => setMobileTab("calling")}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all",
                    mobileTab === "calling"
                      ? "bg-[#0B1324] text-white shadow-[0_4px_12px_rgba(8,23,55,0.3)]"
                      : "text-[#6B7E95]",
                  )}
                >
                  <Phone className="size-4" />
                  {t("tabCalling")}
                </button>
              </div>
            )}
          </div>

          {/* 좌측: 채팅 카드 */}
          <div
            className={cn(
              "lg:h-full min-h-0 transition-all duration-500 ease-in-out",
              isCalling ? "lg:w-1/2" : "lg:w-full",
              mobileTab === "chat" ? "flex-1" : "hidden lg:block",
            )}
          >
            <div className={cn(panelClass, "lg:rounded-3xl")}>
              <ChatContainer />
            </div>
          </div>

          {/* 우측: 이펙트 패널 (calling 중) */}
          <div
            className={cn(
              "lg:h-full min-h-0 transition-all duration-500 ease-in-out overflow-hidden",
              isCalling
                ? cn(
                    "lg:w-1/2",
                    mobileTab === "calling" ? "flex-1" : "hidden lg:block",
                  )
                : "lg:w-0 lg:opacity-0 hidden",
            )}
          >
            {isCalling && callingCallId && (
              <RelayCallProvider
                key={callingCallId}
                callingCallId={callingCallId}
                communicationMode={callingCommunicationMode ?? 'voice_to_voice'}
              >
                <div className={cn(panelClass, "lg:rounded-3xl")}>
                  <CallEffectPanel />
                </div>
              </RelayCallProvider>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
