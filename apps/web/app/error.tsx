'use client';

import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[ErrorBoundary]', error);
  }, [error]);

  return (
    <div className="page-center">
      <div className="page-card max-w-md text-center p-8">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50">
          <svg className="h-6 w-6 text-red-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
          </svg>
        </div>
        <h2 className="mb-2 text-lg font-semibold text-[#0F172A]">
          Something went wrong
        </h2>
        <p className="mb-1 text-sm text-[#64748B]">
          {error.message || 'An unexpected error occurred.'}
        </p>
        {error.digest && (
          <p className="mb-4 font-mono text-xs text-[#94A3B8]">
            Error ID: {error.digest}
          </p>
        )}
        <div className="mt-4 flex justify-center gap-3">
          <button
            onClick={reset}
            className="rounded-xl bg-[#0F172A] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#1E293B] transition-colors"
          >
            Try again
          </button>
          <button
            onClick={() => (window.location.href = '/')}
            className="rounded-xl border border-[#E2E8F0] bg-white px-5 py-2.5 text-sm font-medium text-[#334155] hover:bg-[#F8FAFC] transition-colors"
          >
            Go home
          </button>
        </div>
      </div>
    </div>
  );
}
