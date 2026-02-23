"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { XCircle } from "lucide-react";

export default function PaymentCancelPage() {
  const router = useRouter();
  const t = useTranslations("payment");

  return (
    <div className="page-center">
      <div className="page-card max-w-md px-6 py-8 text-center space-y-6">
        <div className="flex justify-center">
          <div className="w-20 h-20 rounded-full bg-red-50 flex items-center justify-center">
            <XCircle className="w-12 h-12 text-red-500" />
          </div>
        </div>

        <div className="space-y-2">
          <h1 className="text-2xl font-bold text-[#0F172A]">
            {t("cancelTitle")}
          </h1>
          <p className="text-sm text-[#94A3B8]">
            {t("cancelMessage")}
          </p>
        </div>

        <div className="pt-4 space-y-3">
          <button
            onClick={() => router.push("/")}
            className="w-full rounded-xl bg-[#0F172A] text-white py-2.5 text-sm font-medium hover:bg-[#1E293B] transition-colors"
          >
            {t("viewPlans")}
          </button>
          <button
            onClick={() => router.push("/")}
            className="w-full rounded-xl bg-[#F1F5F9] text-[#64748B] py-2.5 text-sm font-medium hover:bg-[#E2E8F0] transition-colors"
          >
            {t("backToHome")}
          </button>
        </div>
      </div>
    </div>
  );
}
