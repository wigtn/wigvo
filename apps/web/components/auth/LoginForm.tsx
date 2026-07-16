'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { createClient } from '@/lib/supabase/client';
import { Loader2, Mail, Lock } from 'lucide-react';

export default function LoginForm() {
  const router = useRouter();
  const t = useTranslations('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submittingRef = useRef(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setError(null);
    setIsLoading(true);

    try {
      const supabase = createClient();

      const { error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });

      if (signInError) {
        if (signInError.message.includes('Invalid login credentials')) {
          setError(t('errors.invalidCredentials'));
        } else if (signInError.message.includes('Email not confirmed')) {
          setError(t('errors.emailNotConfirmed'));
        } else {
          setError(signInError.message);
        }
        return;
      }

      router.push('/');
      router.refresh();
    } catch {
      setError(t('errors.generic'));
    } finally {
      setIsLoading(false);
      submittingRef.current = false;
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* 이메일 */}
      <div className="relative">
        <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 size-4 text-[#94A3B8]" />
        <input
          type="email"
          placeholder={t('email')}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          className="w-full pl-11 pr-4 h-12 rounded-xl bg-white border border-[#E2E8F0] text-[#0F172A] text-sm placeholder:text-[#94A3B8] focus:outline-none focus:border-[#94A3B8] focus:ring-2 focus:ring-[#F1F5F9] transition-all"
        />
      </div>

      {/* 비밀번호 */}
      <div className="relative">
        <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 size-4 text-[#94A3B8]" />
        <input
          type="password"
          placeholder={t('password')}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          className="w-full pl-11 pr-4 h-12 rounded-xl bg-white border border-[#E2E8F0] text-[#0F172A] text-sm placeholder:text-[#94A3B8] focus:outline-none focus:border-[#94A3B8] focus:ring-2 focus:ring-[#F1F5F9] transition-all"
        />
      </div>

      {/* 에러 */}
      {error && (
        <p className="text-sm text-red-600 text-center bg-red-50 border border-red-200 py-2 px-3 rounded-xl">
          {error}
        </p>
      )}

      {/* 로그인 버튼 */}
      <button
        type="submit"
        disabled={isLoading}
        className="w-full h-12 bg-[#0F172A] hover:bg-[#1E293B] text-white font-medium rounded-xl transition-all disabled:opacity-50 shadow-sm flex items-center justify-center gap-2"
      >
        {isLoading ? (
          <>
            <Loader2 className="size-4 animate-spin" />
            {t('submitting')}
          </>
        ) : (
          t('submit')
        )}
      </button>

      {/* 회원가입 */}
      <p className="text-center text-sm text-[#94A3B8]">
        {t('noAccount')}{' '}
        <a href="/signup" className="text-[#0F172A] font-medium hover:underline">
          {t('signUp')}
        </a>
      </p>
    </form>
  );
}
