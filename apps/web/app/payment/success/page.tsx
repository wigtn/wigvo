"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { CheckCircle } from "lucide-react";

function PaymentSuccessContent() {
  const router = useRouter();
  const t = useTranslations("payment");
  const searchParams = useSearchParams();

  useEffect(() => {
    const sessionId = searchParams.get("session_id");
    if (sessionId) {
      // TODO: Verify payment with server
    }
  }, [searchParams]);

  return (
    <div className="page-center">
      <div className="page-card max-w-md px-6 py-8 text-center space-y-6">
        <div className="flex justify-center">
          <div className="w-20 h-20 rounded-full bg-teal-50 flex items-center justify-center">
            <CheckCircle className="w-12 h-12 text-teal-500" />
          </div>
        </div>

        <div className="space-y-2">
          <h1 className="text-2xl font-bold text-[#0F172A]">
            {t("successTitle")}
          </h1>
          <p className="text-sm text-[#94A3B8]">
            {t("successMessage")}
          </p>
        </div>

        <div className="pt-4 space-y-3">
          <button
            onClick={() => router.push("/")}
            className="w-full rounded-xl bg-[#0F172A] text-white py-2.5 text-sm font-medium hover:bg-[#1E293B] transition-colors"
          >
            {t("backToHome")}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function PaymentSuccessPage() {
  return (
    <Suspense
      fallback={
        <div className="page-center">
          <div className="text-[#94A3B8]">Loading...</div>
        </div>
      }
    >
      <PaymentSuccessContent />
    </Suspense>
  );
}
