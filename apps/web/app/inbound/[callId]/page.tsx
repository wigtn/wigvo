'use client';

import { useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Loader2 } from 'lucide-react';
import RealtimeCallView from '@/components/call/RealtimeCallView';
import type { InboundPickupResult } from '@/shared/inbound-types';

const PICKUP_WS_PROTOCOL = 'wigvo.pickup';

export default function InboundCallPage() {
  const params = useParams<{ callId: string }>();
  const router = useRouter();
  const t = useTranslations('inbound');
  const callId = params.callId;
  const [pickup, setPickup] = useState<InboundPickupResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const protocols = useMemo(
    () => pickup ? [PICKUP_WS_PROTOCOL, pickup.pickup_token] : undefined,
    [pickup],
  );

  useEffect(() => {
    let cancelled = false;
    async function refreshPickupToken() {
      try {
        const response = await fetch(`/api/inbound/${callId}/pickup`, { method: 'POST' });
        const payload = (await response.json()) as InboundPickupResult & { error?: string };
        if (!response.ok) throw new Error(payload.error || t('errors.pickup'));
        if (!cancelled) {
          setPickup(payload);
        }
      } catch (pickupError) {
        if (!cancelled) {
          setError(pickupError instanceof Error ? pickupError.message : t('errors.pickup'));
        }
      }
    }
    void refreshPickupToken();
    return () => { cancelled = true; };
  }, [callId, t]);

  if (error) {
    return (
      <div className="page-center">
        <div className="page-card max-w-md px-6 py-12 text-center">
          <p className="text-sm text-red-500">{error}</p>
          <button
            type="button"
            onClick={() => router.push('/inbound')}
            className="mt-4 text-sm text-[#64748B] underline hover:text-[#334155]"
          >
            {t('backToQueue')}
          </button>
        </div>
      </div>
    );
  }

  if (!pickup || !protocols) {
    return (
      <div className="page-center">
        <Loader2 className="size-6 animate-spin text-[#0B1324]" />
      </div>
    );
  }

  return (
    <div className="page-center">
      <div className="page-card h-[80vh] w-full max-w-md p-3">
        <RealtimeCallView
          callId={callId}
          relayWsUrl={pickup.relay_ws_url}
          callMode="relay"
          communicationMode="voice_to_voice"
          targetName={t('caller')}
          wsProtocols={protocols}
          onCallEnd={() => router.push('/inbound')}
        />
      </div>
    </div>
  );
}
