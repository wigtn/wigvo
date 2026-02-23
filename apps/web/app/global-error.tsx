'use client';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html>
      <body className="page-center">
        <div className="page-card max-w-md text-center p-8">
          <h2 className="mb-2 text-lg font-semibold text-[#0F172A]">
            Something went wrong
          </h2>
          <p className="mb-4 text-sm text-[#64748B]">
            {error.message || 'An unexpected error occurred.'}
          </p>
          {error.digest && (
            <p className="mb-4 font-mono text-xs text-[#94A3B8]">
              Digest: {error.digest}
            </p>
          )}
          <div className="flex justify-center gap-3">
            <button
              onClick={reset}
              className="rounded-xl bg-[#0F172A] px-4 py-2 text-sm font-medium text-white hover:bg-[#1E293B]"
            >
              Try again
            </button>
            <button
              onClick={() => (window.location.href = '/')}
              className="rounded-xl border border-[#E2E8F0] bg-white px-4 py-2 text-sm font-medium text-[#334155] hover:bg-[#F8FAFC]"
            >
              Go home
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
