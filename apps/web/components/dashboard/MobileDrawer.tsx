"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { createClient } from "@/lib/supabase/client";
import {
  X,
  MessageSquarePlus,
  History,

  Zap,
  LogOut,
} from "lucide-react";
import LanguageSwitcher from "@/components/common/LanguageSwitcher";
import { useDashboard } from "@/hooks/useDashboard";
import SidebarMenu from "./SidebarMenu";
import { cn } from "@/lib/utils";

interface MobileDrawerProps {
  onNewConversation: () => void;
  onSelectConversation?: (id: string) => void;
}

export default function MobileDrawer({
  onNewConversation,
}: MobileDrawerProps) {
  const router = useRouter();
  const t = useTranslations("sidebar");
  const tCommon = useTranslations("common");
  const {
    isSidebarOpen,
    setSidebarOpen,
    activeMenu,
    setActiveMenu,
  } = useDashboard();

  const handleSignOut = async () => {
    const supabase = createClient();
    await supabase.auth.signOut();
    localStorage.removeItem("currentConversationId");
    router.push("/login");
  };

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSidebarOpen(false);
    };

    if (isSidebarOpen) {
      document.addEventListener("keydown", handleEsc);
      document.body.style.overflow = "hidden";
    }

    return () => {
      document.removeEventListener("keydown", handleEsc);
      document.body.style.overflow = "";
    };
  }, [isSidebarOpen, setSidebarOpen]);

  const handleMenuClick = (menu: "chat" | "conversations") => {
    if (menu === "chat") {
      onNewConversation();
    }
    setSidebarOpen(false);
    setActiveMenu(menu);
  };

  return (
    <>
      {/* 오버레이 */}
      <div
        className={cn(
          "fixed inset-0 bg-black/40 backdrop-blur-sm z-40 transition-opacity duration-300 lg:hidden",
          isSidebarOpen ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
        onClick={() => setSidebarOpen(false)}
      />

      {/* 드로어 */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 w-72 bg-white border-r border-[#E2E8F0] shadow-xl transition-transform duration-300 lg:hidden",
          isSidebarOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        {/* 헤더 */}
        <div className="h-14 flex items-center justify-between px-4 border-b border-[#E2E8F0]">
          <button
            type="button"
            onClick={() => { setActiveMenu("chat"); setSidebarOpen(false); }}
            className="flex items-center gap-2.5 hover:opacity-80 transition-opacity"
          >
            <div className="w-8 h-8 rounded-xl bg-[#F1F5F9] flex items-center justify-center glow-accent">
              <Zap className="size-4 text-[#0F172A]" />
            </div>
            <span className="text-[15px] font-bold tracking-tight text-[#0F172A]">
              WIGVO
            </span>
          </button>
          <button
            onClick={() => setSidebarOpen(false)}
            className="p-2 hover:bg-[#F1F5F9] rounded-lg transition-colors"
          >
            <X className="size-5 text-[#94A3B8]" />
          </button>
        </div>

        {/* 메뉴 */}
        <nav className="px-2 pt-4">
          <p className="px-3 mb-2 text-[10px] font-semibold text-[#94A3B8] uppercase tracking-[0.08em]">
            {t("menu")}
          </p>
          <div className="space-y-0.5">
            <SidebarMenu
              icon={<MessageSquarePlus className="size-[18px]" />}
              label={t("newChat")}
              isCollapsed={false}
              isActive={activeMenu === "chat"}
              onClick={() => handleMenuClick("chat")}
            />
            <SidebarMenu
              icon={<History className="size-[18px]" />}
              label={t("history")}
              isCollapsed={false}
              isActive={activeMenu === "conversations"}
              onClick={() => handleMenuClick("conversations")}
            />
          </div>
        </nav>

        {/* 구분선 */}
        <div className="mx-3 mt-3 border-t border-[#E2E8F0]" />

        {/* 하단 여백 채움 + Language Switcher + 로그아웃 */}
        <div className="flex-1" />
        <div className="px-2 pb-5">
          <div className="px-1 mb-3">
            <LanguageSwitcher />
          </div>
          <div className="mx-1 mb-3 border-t border-[#E2E8F0]" />
          <button
            onClick={handleSignOut}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] text-[#94A3B8] hover:text-red-500 hover:bg-red-50/50 transition-all"
          >
            <LogOut className="size-[18px] shrink-0" />
            <span>{tCommon("logout")}</span>
          </button>
        </div>
      </aside>
    </>
  );
}
