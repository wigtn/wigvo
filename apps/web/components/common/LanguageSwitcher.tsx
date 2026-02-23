'use client';

import { useState, useRef, useEffect } from 'react';
import { useTranslations } from 'next-intl';
import { Globe } from 'lucide-react';
import { changeLocale } from '@/components/providers/I18nProvider';
import { getStoredLocale, type Locale } from '@/lib/i18n';
import { cn } from '@/lib/utils';

interface LanguageSwitcherProps {
  direction?: 'up' | 'down';
  isCollapsed?: boolean;
}

export default function LanguageSwitcher({ direction = 'up', isCollapsed = false }: LanguageSwitcherProps) {
  const t = useTranslations('language');
  const [isOpen, setIsOpen] = useState(false);
  const [currentLocale, setCurrentLocale] = useState<Locale>(() => getStoredLocale());
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Listen for locale changes
    const handleLocaleChange = (e: CustomEvent<Locale>) => {
      setCurrentLocale(e.detail);
    };

    window.addEventListener('localeChange', handleLocaleChange as EventListener);
    return () => {
      window.removeEventListener('localeChange', handleLocaleChange as EventListener);
    };
  }, []);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLocaleChange = (locale: Locale) => {
    changeLocale(locale);
    setIsOpen(false);
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center text-sm font-medium text-[#4A5D76] bg-white/42 backdrop-blur-md border border-white/70 rounded-full shadow-[0_4px_12px_rgba(9,15,26,0.08)] hover:bg-white/56 hover:border-white hover:text-[#1D2B40] transition-all",
          isCollapsed ? "justify-center w-9 h-9 p-0" : "gap-2 px-4 py-2",
        )}
        aria-label="Change language"
      >
        <Globe className="w-4 h-4 shrink-0" />
        {!isCollapsed && <span>{currentLocale === 'ko' ? '한국어' : 'English'}</span>}
      </button>

      {isOpen && (
        <div className={cn(
          "absolute w-36 bg-white/92 backdrop-blur-md border border-white rounded-xl shadow-[0_10px_20px_rgba(9,15,26,0.12)] overflow-hidden z-50",
          direction === 'down' ? 'top-full mt-2' : 'bottom-full mb-2',
          isCollapsed ? 'left-0' : 'right-0',
        )}>
          <button
            onClick={() => handleLocaleChange('en')}
            className={`w-full px-4 py-2.5 text-left text-sm hover:bg-white/70 transition-colors ${
              currentLocale === 'en' ? 'text-[#0B1324] bg-white/70 font-medium' : 'text-[#4A5D76]'
            }`}
          >
            {t('en')}
          </button>
          <button
            onClick={() => handleLocaleChange('ko')}
            className={`w-full px-4 py-2.5 text-left text-sm hover:bg-white/70 transition-colors ${
              currentLocale === 'ko' ? 'text-[#0B1324] bg-white/70 font-medium' : 'text-[#4A5D76]'
            }`}
          >
            {t('ko')}
          </button>
        </div>
      )}
    </div>
  );
}
