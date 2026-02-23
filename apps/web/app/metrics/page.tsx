'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  Loader2,
  AlertTriangle,
  RefreshCw,
  Home,
  BarChart3,
  Clock,
  Phone,
  DollarSign,
  Activity,
  Shield,
  Mic,
  Volume2,
} from 'lucide-react';

interface LatencyStats {
  avg: number;
  std: number;
  min: number;
  max: number;
  count: number;
}

interface MetricsData {
  call_count: number;
  total_calls_queried: number;
  session_a_latency: LatencyStats;
  session_b_e2e_latency: LatencyStats;
  session_b_stt_latency: LatencyStats;
  first_message_latency: LatencyStats;
  turns: LatencyStats;
  duration: LatencyStats & { total_minutes: number };
  echo: {
    total_suppressions: number;
    total_loops: number;
    avg_suppressions_per_call: number;
    avg_loops_per_call: number;
  };
  vad: {
    total_false_triggers: number;
    avg_per_call: number;
  };
  hallucinations: {
    total_blocked: number;
    avg_per_call: number;
  };
  cost: {
    total_tokens: number;
    total_usd: number;
    avg_per_call: number;
    avg_per_minute: number;
  };
  by_mode: Record<string, {
    call_count: number;
    avg_session_a_ms: number;
    avg_session_b_ms: number;
    avg_turns: number;
  }>;
}

const MODE_LABELS: Record<string, string> = {
  voice_to_voice: 'Voice-to-Voice',
  text_to_voice: 'Text-to-Voice',
  voice_to_text: 'Voice-to-Text',
  full_agent: 'Full Agent',
};

function StatCard({ icon: Icon, label, value, sub }: {
  icon: typeof BarChart3;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white px-5 py-4">
      <div className="flex items-center gap-2 mb-2">
        <div className="w-7 h-7 rounded-lg bg-[#F1F5F9] flex items-center justify-center">
          <Icon className="size-3.5 text-[#64748B]" />
        </div>
        <p className="text-xs font-medium text-[#94A3B8] uppercase tracking-wider">{label}</p>
      </div>
      <p className="text-2xl font-bold text-[#0F172A] tabular-nums">{value}</p>
      {sub && <p className="text-xs text-[#64748B] mt-0.5">{sub}</p>}
    </div>
  );
}

function LatencyTable({ data }: { data: MetricsData }) {
  const rows = [
    { label: 'Session A (User→Recipient)', stats: data.session_a_latency },
    { label: 'Session B E2E (Recipient→User)', stats: data.session_b_e2e_latency },
    { label: 'Session B STT', stats: data.session_b_stt_latency },
    { label: 'First Message', stats: data.first_message_latency },
  ];

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white overflow-hidden">
      <div className="px-5 py-3 border-b border-[#E2E8F0] bg-[#F8FAFC]">
        <h3 className="text-sm font-semibold text-[#0F172A]">Latency (ms)</h3>
        <p className="text-[10px] text-[#94A3B8]">Copy directly to ACL paper Table</p>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#E2E8F0] text-[#64748B]">
            <th className="text-left px-5 py-2.5 font-medium text-xs">Metric</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Avg</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Std</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Min</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Max</th>
            <th className="text-right px-5 py-2.5 font-medium text-xs">N</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-b border-[#F1F5F9] last:border-0">
              <td className="px-5 py-2.5 text-[#1E293B] font-medium text-xs">{row.label}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs">{row.stats.avg}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs text-[#94A3B8]">{row.stats.std}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs">{row.stats.min}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs">{row.stats.max}</td>
              <td className="text-right px-5 py-2.5 tabular-nums text-xs text-[#94A3B8]">{row.stats.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModeTable({ data }: { data: MetricsData }) {
  const modes = Object.entries(data.by_mode);
  if (modes.length === 0) return null;

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white overflow-hidden">
      <div className="px-5 py-3 border-b border-[#E2E8F0] bg-[#F8FAFC]">
        <h3 className="text-sm font-semibold text-[#0F172A]">By Communication Mode</h3>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#E2E8F0] text-[#64748B]">
            <th className="text-left px-5 py-2.5 font-medium text-xs">Mode</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Calls</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Avg A (ms)</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Avg B (ms)</th>
            <th className="text-right px-5 py-2.5 font-medium text-xs">Avg Turns</th>
          </tr>
        </thead>
        <tbody>
          {modes.map(([mode, modeData]) => (
            <tr key={mode} className="border-b border-[#F1F5F9] last:border-0">
              <td className="px-5 py-2.5 text-[#1E293B] font-medium text-xs">{MODE_LABELS[mode] || mode}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs">{modeData.call_count}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs">{modeData.avg_session_a_ms}</td>
              <td className="text-right px-3 py-2.5 tabular-nums text-xs">{modeData.avg_session_b_ms}</td>
              <td className="text-right px-5 py-2.5 tabular-nums text-xs">{modeData.avg_turns}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QualityTable({ data }: { data: MetricsData }) {
  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white overflow-hidden">
      <div className="px-5 py-3 border-b border-[#E2E8F0] bg-[#F8FAFC]">
        <h3 className="text-sm font-semibold text-[#0F172A]">Quality Metrics</h3>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#E2E8F0] text-[#64748B]">
            <th className="text-left px-5 py-2.5 font-medium text-xs">Metric</th>
            <th className="text-right px-3 py-2.5 font-medium text-xs">Total</th>
            <th className="text-right px-5 py-2.5 font-medium text-xs">Avg/Call</th>
          </tr>
        </thead>
        <tbody>
          <tr className="border-b border-[#F1F5F9]">
            <td className="px-5 py-2.5 text-[#1E293B] font-medium text-xs">Echo Suppressions</td>
            <td className="text-right px-3 py-2.5 tabular-nums text-xs">{data.echo.total_suppressions}</td>
            <td className="text-right px-5 py-2.5 tabular-nums text-xs">{data.echo.avg_suppressions_per_call}</td>
          </tr>
          <tr className="border-b border-[#F1F5F9]">
            <td className="px-5 py-2.5 text-[#1E293B] font-medium text-xs">Echo Loops Detected</td>
            <td className="text-right px-3 py-2.5 tabular-nums text-xs">{data.echo.total_loops}</td>
            <td className="text-right px-5 py-2.5 tabular-nums text-xs">{data.echo.avg_loops_per_call}</td>
          </tr>
          <tr className="border-b border-[#F1F5F9]">
            <td className="px-5 py-2.5 text-[#1E293B] font-medium text-xs">VAD False Triggers</td>
            <td className="text-right px-3 py-2.5 tabular-nums text-xs">{data.vad.total_false_triggers}</td>
            <td className="text-right px-5 py-2.5 tabular-nums text-xs">{data.vad.avg_per_call}</td>
          </tr>
          <tr className="last:border-0">
            <td className="px-5 py-2.5 text-[#1E293B] font-medium text-xs">STT Hallucinations Blocked</td>
            <td className="text-right px-3 py-2.5 tabular-nums text-xs">{data.hallucinations.total_blocked}</td>
            <td className="text-right px-5 py-2.5 tabular-nums text-xs">{data.hallucinations.avg_per_call}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export default function MetricsPage() {
  const router = useRouter();
  const [data, setData] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchedRef = useRef(false);

  useEffect(() => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;

    async function fetchMetrics() {
      try {
        const res = await fetch('/api/metrics');
        if (res.status === 401) {
          router.push('/login');
          return;
        }
        if (!res.ok) {
          setError('Failed to load metrics');
          setLoading(false);
          return;
        }
        const json = await res.json();
        setData(json);
        setLoading(false);
      } catch {
        setError('Network error');
        setLoading(false);
      }
    }

    fetchMetrics();
  }, [router]);

  if (loading) {
    return (
      <div className="page-center">
        <div className="page-card max-w-md flex flex-col items-center gap-3 py-16">
        <Loader2 className="size-6 text-[#0F172A] animate-spin" />
        <p className="text-sm text-[#94A3B8]">Loading metrics...</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="page-center">
        <div className="page-card max-w-md flex flex-col items-center gap-4 py-16">
        <div className="w-14 h-14 rounded-2xl bg-red-50 flex items-center justify-center">
          <AlertTriangle className="size-6 text-red-500" />
        </div>
        <p className="font-medium text-red-600 text-sm">{error}</p>
        <button
          onClick={() => window.location.reload()}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-medium bg-white border border-[#E2E8F0] text-[#64748B] hover:bg-[#F8FAFC] transition-all"
        >
          <RefreshCw className="size-3.5" />
          Retry
        </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto page-shell">
      <div className="mx-auto max-w-4xl px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[#0F172A] flex items-center justify-center">
              <BarChart3 className="size-4 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#0F172A] tracking-tight">Call Metrics</h1>
              <p className="text-xs text-[#94A3B8]">ACL 2026 Demo — Evaluation Data</p>
            </div>
          </div>
          <button
            onClick={() => router.push('/')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[#64748B] hover:text-[#334155] hover:bg-[#F1F5F9] transition-all"
          >
            <Home className="size-3.5" />
            Home
          </button>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <StatCard
            icon={Phone}
            label="Total Calls"
            value={String(data.call_count)}
            sub={`of ${data.total_calls_queried} completed`}
          />
          <StatCard
            icon={Clock}
            label="Avg Session A"
            value={`${data.session_a_latency.avg}ms`}
            sub={`${data.session_a_latency.count} measurements`}
          />
          <StatCard
            icon={Activity}
            label="Avg Session B"
            value={`${data.session_b_e2e_latency.avg}ms`}
            sub={`E2E latency`}
          />
          <StatCard
            icon={DollarSign}
            label="Total Cost"
            value={`$${data.cost.total_usd}`}
            sub={`$${data.cost.avg_per_minute}/min`}
          />
        </div>

        {/* Second row cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
          <StatCard
            icon={Mic}
            label="Avg Turns"
            value={String(data.turns.avg)}
            sub={`per call`}
          />
          <StatCard
            icon={Volume2}
            label="Echo Gate"
            value={`${data.echo.total_suppressions}`}
            sub={`${data.echo.total_loops} loops detected`}
          />
          <StatCard
            icon={Shield}
            label="Hallucinations"
            value={String(data.hallucinations.total_blocked)}
            sub={`blocked`}
          />
          <StatCard
            icon={Clock}
            label="First Message"
            value={`${data.first_message_latency.avg}ms`}
            sub={`avg latency`}
          />
        </div>

        {/* Tables */}
        <div className="space-y-6">
          <LatencyTable data={data} />
          <ModeTable data={data} />
          <QualityTable data={data} />
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-[10px] text-[#94A3B8]">
          WIGVO — AI Realtime Relay Platform
        </div>
      </div>
    </div>
  );
}
