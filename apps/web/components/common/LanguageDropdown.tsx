'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import { SUPPORTED_LANGUAGES } from '@/shared/call-types';

interface LanguageDropdownProps {
  value: string;
  onChange: (code: string) => void;
  disabled?: boolean;
}

export default function LanguageDropdown({ value, onChange, disabled = false }: LanguageDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const selected = SUPPORTED_LANGUAGES.find((l) => l.code === value);

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Close on Escape
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setIsOpen(false);
    }
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen]);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => !disabled && setIsOpen((o) => !o)}
        disabled={disabled}
        className="w-full h-11 flex items-center justify-between px-3 text-sm rounded-xl border border-white/80 bg-white/65 backdrop-blur-md text-[#0F172A] transition-colors hover:bg-white/80 focus:outline-none focus:border-[#CBD5E1] disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <span className="flex items-center gap-2">
          <span>{selected?.flag}</span>
          <span>{selected?.label}</span>
        </span>
        <ChevronDown className={`size-4 text-[#94A3B8] transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      <div
        className={`absolute left-0 right-0 top-full mt-1 z-50 rounded-xl border border-white/80 bg-white/92 shadow-lg overflow-hidden transition-all duration-200 origin-top backdrop-blur-md ${
          isOpen ? 'opacity-100 scale-y-100 translate-y-0' : 'opacity-0 scale-y-95 -translate-y-1 pointer-events-none'
        }`}
      >
        {SUPPORTED_LANGUAGES.map((lang) => {
          const isSelected = lang.code === value;
          return (
            <button
              key={lang.code}
              type="button"
              onClick={() => {
                onChange(lang.code);
                setIsOpen(false);
              }}
              className={`w-full flex items-center justify-between px-3 py-2.5 text-sm transition-colors ${
                isSelected
                  ? 'bg-[#F1F5F9] font-medium text-[#0F172A]'
                  : 'text-[#64748B] hover:bg-[#F8FAFC]'
              }`}
            >
              <span className="flex items-center gap-2">
                <span>{lang.flag}</span>
                <span>{lang.label}</span>
              </span>
              {isSelected && <Check className="size-4 text-[#0F172A]" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}
