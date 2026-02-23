'use client';

import { useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { createClient } from '@/lib/supabase/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Loader2, Mail, Lock, User, ArrowLeft } from 'lucide-react';
import LanguageSwitcher from '@/components/common/LanguageSwitcher';

export default function SignupPage() {
  const t = useTranslations('signup');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // 비밀번호 확인
    if (password !== confirmPassword) {
      setError(t('errors.passwordMismatch'));
      return;
    }

    if (password.length < 6) {
      setError(t('errors.passwordTooShort'));
      return;
    }

    setIsLoading(true);

    try {
      const supabase = createClient();
      
      const { error: signUpError } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: {
            name,
          },
          emailRedirectTo: `${window.location.origin}/auth/callback`,
        },
      });

      if (signUpError) {
        if (signUpError.message.includes('already registered')) {
          setError(t('errors.emailExists'));
        } else {
          setError(signUpError.message);
        }
        return;
      }

      setSuccess(true);
    } catch {
      setError(t('errors.generic'));
    } finally {
      setIsLoading(false);
    }
  };

  if (success) {
    return (
      <div className="page-shell page-center">
        <div className="page-card max-w-md px-6 py-8 space-y-6 text-center">
          <div className="flex flex-col items-center gap-4">
            <div className="w-16 h-16 rounded-full bg-green-50 border border-green-200 flex items-center justify-center">
              <Mail className="size-8 text-green-600" />
            </div>
            <h2 className="text-xl font-bold text-[#0F172A]">{t('success.title')}</h2>
            <p className="text-[#64748B] text-sm">
              <span className="text-[#0F172A] font-medium">{email} </span>
              {t('success.message')}
            </p>
          </div>
          <Link href="/login">
            <Button
              variant="outline"
              className="w-full h-12 border-[#E2E8F0] text-[#334155] hover:bg-[#F8FAFC]"
            >
              {t('success.backToLogin')}
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="page-shell page-center">
      {/* Language Switcher - 우상단 고정 */}
      <div className="absolute top-4 right-4">
        <LanguageSwitcher direction="down" />
      </div>

      <div className="page-card max-w-md px-6 py-8 space-y-6">
        {/* 뒤로가기 */}
        <Link
          href="/login"
          className="inline-flex items-center gap-1 text-[#64748B] hover:text-[#334155] text-sm"
        >
          <ArrowLeft className="size-4" />
          {t('backToLogin')}
        </Link>

        {/* 로고 & 설명 */}
        <div className="text-center space-y-3">
          <div className="flex flex-col items-center justify-center gap-3">
            <Image
              src="/logo.png"
              alt="WIGVO Logo"
              width={60}
              height={60}
              className="rounded-full border border-[#E2E8F0]"
            />
            <h1 className="text-2xl font-bold text-[#0F172A]">
              {t('title')}
            </h1>
          </div>
        </div>

        {/* 회원가입 폼 */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* 이름 입력 */}
          <div className="relative">
            <User className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-gray-500" />
            <Input
              type="text"
              placeholder={t('name')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="pl-10 h-12 bg-white border-[#E2E8F0] text-[#0F172A] placeholder:text-[#94A3B8] focus:border-[#94A3B8] focus:ring-[#F1F5F9]"
            />
          </div>

          {/* 이메일 입력 */}
          <div className="relative">
            <Mail className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-gray-500" />
            <Input
              type="email"
              placeholder={t('email')}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="pl-10 h-12 bg-white border-[#E2E8F0] text-[#0F172A] placeholder:text-[#94A3B8] focus:border-[#94A3B8] focus:ring-[#F1F5F9]"
            />
          </div>

          {/* 비밀번호 입력 */}
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-gray-500" />
            <Input
              type="password"
              placeholder={t('password')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              className="pl-10 h-12 bg-white border-[#E2E8F0] text-[#0F172A] placeholder:text-[#94A3B8] focus:border-[#94A3B8] focus:ring-[#F1F5F9]"
            />
          </div>

          {/* 비밀번호 확인 */}
          <div className="relative">
            <Lock className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-gray-500" />
            <Input
              type="password"
              placeholder={t('confirmPassword')}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              className="pl-10 h-12 bg-white border-[#E2E8F0] text-[#0F172A] placeholder:text-[#94A3B8] focus:border-[#94A3B8] focus:ring-[#F1F5F9]"
            />
          </div>

          {/* 에러 메시지 */}
          {error && (
            <p className="text-sm text-red-600 text-center bg-red-50 border border-red-200 py-2 px-3 rounded-lg">
              {error}
            </p>
          )}

          {/* 회원가입 버튼 */}
          <Button
            type="submit"
            disabled={isLoading}
            className="w-full h-12 bg-[#0F172A] hover:bg-[#1E293B] text-white font-medium rounded-xl"
          >
            {isLoading ? (
              <>
                <Loader2 className="size-4 animate-spin mr-2" />
                {t('submitting')}
              </>
            ) : (
              t('submit')
            )}
          </Button>
        </form>

        {/* 이용약관 안내 */}
        <p className="text-center text-xs text-[#94A3B8] px-4">
          {t('terms')}
        </p>
      </div>
    </div>
  );
}
