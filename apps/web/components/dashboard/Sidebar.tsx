"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { createClient } from "@/lib/supabase/client";
import {
  MessageSquarePlus,
  History,
  ChevronLeft,
  ChevronRight,
  Zap,
  LogOut,
} from "lucide-react";
import LanguageSwitcher from "@/components/common/LanguageSwitcher";
import SidebarMenu from "./SidebarMenu";
import { useDashboard } from "@/hooks/useDashboard";
import { cn } from "@/lib/utils";

interface SidebarProps {
  onNewConversation: () => void;
  onSelectConversation?: (id: string) => void;
}

export default function Sidebar({
  onNewConversation,
}: SidebarProps) {
  const router = useRouter();
  const t = useTranslations("sidebar");
  const tCommon = useTranslations("common");
  const {
    isSidebarCollapsed,
    setSidebarCollapsed,
    activeMenu,
    setActiveMenu,
  } = useDashboard();

  const handleMenuClick = (menu: "chat" | "conversations") => {
    if (menu === "chat") {
      onNewConversation();
    }
    setActiveMenu(menu);
  };

  const handleSignOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    localStorage.removeItem("currentConversationId");
    localStorage.removeItem("currentCommunicationMode");
    localStorage.removeItem("currentSourceLang");
    localStorage.removeItem("currentTargetLang");
    router.push("/login");
  };

  return (
    <aside
      className={cn(
        "relative h-full bg-white/46 backdrop-blur-xl border-r border-white/72 shadow-[0_18px_36px_rgba(9,15,26,0.16)] transition-all duration-300 flex flex-col",
        isSidebarCollapsed ? "w-16" : "w-60",
      )}
    >
      {/* 로고 — 클릭 시 메인 대시보드(포탈)로 이동 */}
      <button
        type="button"
        onClick={() => setActiveMenu("chat")}
        className={cn(
          "h-14 flex items-center border-b border-white/75 px-4 w-full hover:bg-white/42 transition-colors",
          isSidebarCollapsed && "justify-center px-2",
        )}
      >
        <div className="w-8 h-8 rounded-xl bg-[#0B1324] flex items-center justify-center shrink-0 shadow-[0_8px_18px_rgba(8,23,55,0.26)]">
          <Zap className="size-4 text-white" />
        </div>
        {!isSidebarCollapsed && (
          <span className="ml-2.5 text-[15px] font-bold tracking-tight text-[#0B1324]">
            WIGVO
          </span>
        )}
      </button>

      {/* 접기/펼치기 */}
      <button
        onClick={() => setSidebarCollapsed(!isSidebarCollapsed)}
        className="absolute -right-3 top-16 z-10 bg-white/85 border border-white rounded-full p-1 shadow-[0_4px_10px_rgba(9,15,26,0.12)] hover:bg-white transition-colors"
      >
        {isSidebarCollapsed ? (
          <ChevronRight className="size-3.5 text-[#7A8AA0]" />
        ) : (
          <ChevronLeft className="size-3.5 text-[#7A8AA0]" />
        )}
      </button>

      {/* 메뉴 */}
      <nav className="px-2 pt-4">
        {!isSidebarCollapsed && (
          <p className="px-3 mb-2 text-[10px] font-semibold text-[#7890A8] uppercase tracking-[0.08em]">
            {t("menu")}
          </p>
        )}
        <div className="space-y-0.5">
          <SidebarMenu
            icon={<MessageSquarePlus className="size-[18px]" />}
            label={t("newChat")}
            isCollapsed={isSidebarCollapsed}
            isActive={activeMenu === "chat"}
            onClick={() => handleMenuClick("chat")}
          />
          <SidebarMenu
            icon={<History className="size-[18px]" />}
            label={t("history")}
            isCollapsed={isSidebarCollapsed}
            isActive={activeMenu === "conversations"}
            onClick={() => handleMenuClick("conversations")}
          />
        </div>
      </nav>

      {/* 구분선 */}
      {!isSidebarCollapsed && (
        <div className="mx-3 mt-3 border-t border-white/70" />
      )}

      <div className="flex-1" />

      {/* Language Switcher + 로그아웃 */}
      <div className="px-2 pb-4">
        <div className={cn("mb-3", isSidebarCollapsed ? "flex justify-center" : "px-1")}>
          <LanguageSwitcher isCollapsed={isSidebarCollapsed} />
        </div>
        <div className="mx-1 mb-3 border-t border-white/70" />
        <button
          onClick={handleSignOut}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] text-[#7890A8] hover:text-red-500 hover:bg-red-50/55 transition-all",
            isSidebarCollapsed && "justify-center px-2",
          )}
        >
          <LogOut className="size-[18px] shrink-0" />
          {!isSidebarCollapsed && <span>{tCommon("logout")}</span>}
        </button>
      </div>
    </aside>
  );
}
