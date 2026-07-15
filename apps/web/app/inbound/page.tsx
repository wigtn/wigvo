'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { ArrowLeft, Loader2, PhoneIncoming, RefreshCw } from 'lucide-react';
import type { InboundCall, InboundPickupResult } from '@/shared/inbound-types';

const POLL_INTERVAL_MS = 3000;

export default function InboundQueuePage() {
  const router = useRouter();
  const t = useTranslations('inbound');
  const [calls, setCalls] = useState<InboundCall[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [claimingId, setClaimingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCalls = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const response = await fetch('/api/inbound', { cache: 'no-store' });
      if (!response.ok) throw new Error(t('errors.load'));
      const payload = (await response.json()) as { calls: InboundCall[] };
      setCalls(payload.calls);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : t('errors.load'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    void loadCalls(true);
    const timer = window.setInterval(() => void loadCalls(true), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadCalls]);

  const pickup = useCallback(async (callId: string) => {
    setClaimingId(callId);
    setError(null);
    try {
      const response = await fetch(`/api/inbound/${callId}/pickup`, { method: 'POST' });
      const payload = (await response.json()) as InboundPickupResult & { error?: string };
      if (!response.ok) {
        if (response.status === 409) throw new Error(t('errors.claimed'));
        if (response.status === 403) throw new Error(t('errors.forbidden'));
        throw new Error(payload.error || t('errors.pickup'));
      }
      router.push(`/inbound/${callId}`);
    } catch (pickupError) {
      setError(pickupError instanceof Error ? pickupError.message : t('errors.pickup'));
      await loadCalls(true);
    } finally {
      setClaimingId(null);
    }
  }, [loadCalls, router, t]);

  return (
    <main className="min-h-full dashboard-shell px-4 py-6 md:px-8 md:py-10">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => router.push('/')}
              className="rounded-xl border border-white/70 bg-white/65 p-2.5 text-[#44546A] transition-colors hover:bg-white"
              aria-label={t('back')}
            >
              <ArrowLeft className="size-4" />
            </button>
            <div>
              <h1 className="text-xl font-bold text-[#0B1324]">{t('title')}</h1>
              <p className="mt-1 text-sm text-[#6B7E95]">{t('subtitle')}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void loadCalls()}
            disabled={refreshing}
            className="rounded-xl border border-white/70 bg-white/65 p-2.5 text-[#44546A] transition-colors hover:bg-white disabled:opacity-50"
            aria-label={t('refresh')}
          >
            <RefreshCw className={`size-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
            {error}
          </div>
        )}

        <section className="dashboard-panel overflow-hidden rounded-3xl">
          {loading ? (
            <div className="flex min-h-72 items-center justify-center">
              <Loader2 className="size-6 animate-spin text-[#0B1324]" />
            </div>
          ) : calls.length === 0 ? (
            <div className="flex min-h-72 flex-col items-center justify-center px-6 text-center">
              <div className="mb-4 rounded-2xl bg-white/70 p-4">
                <PhoneIncoming className="size-7 text-[#7890A8]" />
              </div>
              <p className="font-medium text-[#0B1324]">{t('emptyTitle')}</p>
              <p className="mt-1 text-sm text-[#7890A8]">{t('emptyDescription')}</p>
            </div>
          ) : (
            <ul className="divide-y divide-white/70">
              {calls.map((call, index) => {
                const languages = call.languages.length >= 2
                  ? `${call.languages[0].toUpperCase()} → ${call.languages[1].toUpperCase()}`
                  : t('defaultLanguages');
                return (
                  <li key={call.call_id} className="flex items-center gap-4 px-5 py-4">
                    <div className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-[#0B1324] text-white">
                      <PhoneIncoming className="size-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="font-semibold text-[#0B1324]">{t('callLabel', { number: index + 1 })}</p>
                        <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                          {t('waiting')}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-[#7890A8]">
                        {languages} · {new Date(call.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => void pickup(call.call_id)}
                      disabled={claimingId !== null}
                      className="inline-flex min-w-24 items-center justify-center gap-2 rounded-xl bg-[#0B1324] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#1E293B] disabled:opacity-50"
                    >
                      {claimingId === call.call_id && <Loader2 className="size-4 animate-spin" />}
                      {claimingId === call.call_id ? t('connecting') : t('pickup')}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </main>
  );
}
