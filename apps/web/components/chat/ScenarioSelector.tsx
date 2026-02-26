'use client';

import { useState, useCallback } from 'react';
import { useTranslations, useLocale } from 'next-intl';
import {
  ArrowLeftRight,
  Phone,
  Mic,
  MessageSquare,
  Bot,
  ChevronLeft,
  Send,
  UtensilsCrossed,
  Scissors,
  Stethoscope,
  Building2,
  Search,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ScenarioType, ScenarioSubType } from '@/shared/types';
import type { CommunicationMode, CallCategory } from '@/shared/call-types';
import { SUPPORTED_LANGUAGES, getDefaultLanguagePairForLocale, resolveDirectMode } from '@/shared/call-types';
import type { DirectCallOptions } from '@/shared/call-types';
import LanguageDropdown from '@/components/common/LanguageDropdown';

// ── Quick Action (AI Auto only) ────────────────────────────────
interface QuickAction {
  icon: LucideIcon;
  scenarioType: ScenarioType;
  subType: ScenarioSubType;
  labelKey: string;
  descKey: string;
}

const QUICK_ACTIONS: QuickAction[] = [
  { icon: UtensilsCrossed, scenarioType: 'RESERVATION', subType: 'RESTAURANT', labelKey: 'restaurant', descKey: 'restaurantDesc' },
  { icon: Scissors, scenarioType: 'RESERVATION', subType: 'SALON', labelKey: 'salon', descKey: 'salonDesc' },
  { icon: Stethoscope, scenarioType: 'RESERVATION', subType: 'HOSPITAL', labelKey: 'hospital', descKey: 'hospitalDesc' },
  { icon: Building2, scenarioType: 'RESERVATION', subType: 'HOTEL', labelKey: 'hotel', descKey: 'hotelDesc' },
  { icon: Search, scenarioType: 'INQUIRY', subType: 'OTHER', labelKey: 'inquiry', descKey: 'inquiryDesc' },
  { icon: Wrench, scenarioType: 'AS_REQUEST', subType: 'OTHER', labelKey: 'asRequest', descKey: 'asRequestDesc' },
];

// ── Props ───────────────────────────────────────────────────────
interface ScenarioSelectorProps {
  onSelect: (
    scenarioType: ScenarioType,
    subType: ScenarioSubType,
    communicationMode: CommunicationMode,
    sourceLang: string,
    targetLang: string,
  ) => void;
  disabled?: boolean;
}

type Step = 'category' | 'direct' | 'ai_auto';

export function ScenarioSelector({ onSelect, disabled = false }: ScenarioSelectorProps) {
  const t = useTranslations('scenario');
  const tCat = useTranslations('scenario.category');
  const tDirect = useTranslations('scenario.direct');
  const tAiAuto = useTranslations('scenario.aiAuto');
  const tQuick = useTranslations('scenario.quick');
  const tLang = useTranslations('scenario.lang');
  const locale = useLocale();

  // Language pair (locale 기반 기본값: UI 언어 = source, 상대방 = target)
  const defaultPair = getDefaultLanguagePairForLocale(locale);
  const [sourceLang, setSourceLang] = useState(defaultPair.source.code);
  const [targetLang, setTargetLang] = useState(defaultPair.target.code);

  // Step navigation
  const [step, setStep] = useState<Step>('category');

  // Direct call options
  const [inputMethod, setInputMethod] = useState<'voice' | 'text'>('voice');

  // AI Auto free text
  const [freeText, setFreeText] = useState('');
  const handleSwapLanguages = () => {
    setSourceLang(targetLang);
    setTargetLang(sourceLang);
  };

  const sourceLangObj = SUPPORTED_LANGUAGES.find((l) => l.code === sourceLang);
  const targetLangObj = SUPPORTED_LANGUAGES.find((l) => l.code === targetLang);

  // ── Category selection ──────────────────────────────────────
  const handleCategorySelect = useCallback((category: CallCategory) => {
    if (disabled) return;
    if (category === 'direct') {
      setStep('direct');
    } else {
      setStep('ai_auto');
    }
  }, [disabled]);

  const handleBack = useCallback(() => {
    setStep('category');
  }, []);

  // ── Direct call: start ──────────────────────────────────────
  const handleDirectStart = useCallback(() => {
    if (disabled) return;
    const options: DirectCallOptions = {
      translation: true,
      inputMethod,
    };
    const mode = resolveDirectMode(options);
    onSelect('INQUIRY', 'OTHER', mode, sourceLang, targetLang);
  }, [disabled, inputMethod, sourceLang, targetLang, onSelect]);

  // ── AI Auto: quick action ───────────────────────────────────
  const handleQuickAction = useCallback(
    (action: QuickAction) => {
      if (disabled) return;
      onSelect(action.scenarioType, action.subType, 'full_agent', sourceLang, targetLang);
    },
    [sourceLang, targetLang, disabled, onSelect],
  );

  // ── AI Auto: free text ──────────────────────────────────────
  const handleFreeTextSubmit = useCallback(() => {
    const text = freeText.trim();
    if (!text || disabled) return;
    onSelect('INQUIRY', 'OTHER', 'full_agent', sourceLang, targetLang);
  }, [freeText, sourceLang, targetLang, disabled, onSelect]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleFreeTextSubmit();
      }
    },
    [handleFreeTextSubmit],
  );

  // ── Language pair section ───────────────────────────────────
  const renderLanguagePair = () => (
    <div className="mb-8 max-w-2xl mx-auto w-full">
      <div className="glass-surface rounded-3xl px-4 py-4 md:px-5 md:py-5">
        <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-3 md:gap-4">
          <p className="text-[10px] font-semibold text-[#94A3B8] uppercase tracking-wider mb-1.5">
            {tLang('myLang')}
          </p>
          <div />
          <p className="text-[10px] font-semibold text-[#94A3B8] uppercase tracking-wider mb-1.5 text-left">
            {tLang('theirLang')}
          </p>

          <div>
            <LanguageDropdown value={sourceLang} onChange={setSourceLang} disabled={disabled} />
          </div>
          <button
            type="button"
            onClick={handleSwapLanguages}
            disabled={disabled}
            className="self-center justify-self-center shrink-0 w-10 h-10 rounded-full bg-white/75 border border-white/80 flex items-center justify-center text-[#64748B] hover:bg-white hover:text-[#0F172A] transition-colors disabled:opacity-50"
            aria-label="Swap languages"
          >
            <ArrowLeftRight className="size-4" />
          </button>
          <div>
            <LanguageDropdown value={targetLang} onChange={setTargetLang} disabled={disabled} />
          </div>
        </div>
      </div>

      {/* Translation flow preview */}
      <div className="mt-3 px-1 space-y-1">
        <p className="text-[10px] text-[#94A3B8] flex items-center gap-1">
          <span>{sourceLangObj?.flag}</span>
          <span>{tLang('flowSend', { source: sourceLangObj?.label ?? '', target: targetLangObj?.label ?? '' })}</span>
        </p>
        <p className="text-[10px] text-[#94A3B8] flex items-center gap-1">
          <span>{targetLangObj?.flag}</span>
          <span>{tLang('flowReceive', { source: sourceLangObj?.label ?? '' })}</span>
        </p>
      </div>
    </div>
  );

  // ── Step-specific content helpers ───────────────────────────
  const renderCategoryContent = () => (
    <>
      {/* Header */}
      <div className="text-center mb-8 max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold text-[#0F172A] tracking-tight mb-1.5">
          {t.rich('title', { accent: (chunks) => <span className="text-gradient">{chunks}</span> })}
        </h2>
        <p className="text-sm text-[#94A3B8]">{t('subtitle')}</p>
      </div>

      {/* Category cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl mx-auto w-full">
        {/* Direct Call */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => handleCategorySelect('direct')}
          className="glass-surface group flex items-center gap-4 p-5 rounded-3xl hover:border-white/90 hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)] active:scale-[0.99] transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed text-left"
        >
          <div className="shrink-0 w-12 h-12 rounded-2xl bg-[#F1F5F9] border border-[#E2E8F0] flex items-center justify-center">
            <Phone className="size-5 text-[#0F172A]" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-[#0F172A]">{tCat('directTitle')}</p>
            <p className="text-[10px] text-[#94A3B8] font-medium uppercase tracking-wider">{tCat('directSubtitle')}</p>
            <p className="text-xs text-[#64748B] mt-0.5 leading-snug">{tCat('directDesc')}</p>
          </div>
        </button>

        {/* AI Auto Call */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => handleCategorySelect('ai_auto')}
          className="glass-surface group flex items-center gap-4 p-5 rounded-3xl hover:border-white/90 hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)] active:scale-[0.99] transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed text-left"
        >
          <div className="shrink-0 w-12 h-12 rounded-2xl bg-[#F1F5F9] border border-[#E2E8F0] flex items-center justify-center">
            <Bot className="size-5 text-[#0F172A]" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-[#0F172A]">{tCat('aiAutoTitle')}</p>
            <p className="text-[10px] text-[#94A3B8] font-medium uppercase tracking-wider">{tCat('aiAutoSubtitle')}</p>
            <p className="text-xs text-[#64748B] mt-0.5 leading-snug">{tCat('aiAutoDesc')}</p>
          </div>
        </button>
      </div>
    </>
  );

  const renderDirectContent = () => (
    <>
      {/* Header */}
      <div className="text-center mb-6 max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold text-[#0F172A] tracking-tight mb-1.5">
          {tDirect('title')}
        </h2>
        <p className="text-sm text-[#94A3B8]">{tDirect('subtitle')}</p>
      </div>

      <div className="max-w-2xl mx-auto w-full space-y-5">
        {/* Input method */}
        <div>
          <p className="text-xs font-semibold text-[#64748B] mb-2 uppercase tracking-wider">
            {tDirect('inputLabel')}
          </p>
          <div className="grid grid-cols-2 gap-2 items-stretch">
            <button
              type="button"
              disabled={disabled}
              onClick={() => setInputMethod('voice')}
              className={`glass-surface box-border min-h-[158px] flex flex-col items-center justify-center gap-2 p-5 rounded-3xl transition-[background-color,border-color,box-shadow,color] ${
                inputMethod === 'voice'
                  ? 'glass-surface-selected'
                  : 'hover:border-white/90 hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)]'
              } disabled:opacity-50`}
            >
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                inputMethod === 'voice' ? 'bg-[#0F172A] text-white' : 'bg-[#F1F5F9] text-[#64748B]'
              }`}>
                <Mic className="size-5" />
              </div>
              <span className="text-xs font-semibold text-[#0F172A]">{tDirect('inputVoice')}</span>
              <span className="text-[9px] text-[#94A3B8] text-center leading-tight">{tDirect('inputVoiceDesc')}</span>
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setInputMethod('text')}
              className={`glass-surface box-border min-h-[158px] flex flex-col items-center justify-center gap-2 p-5 rounded-3xl transition-[background-color,border-color,box-shadow,color] ${
                inputMethod === 'text'
                  ? 'glass-surface-selected'
                  : 'hover:border-white/90 hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)]'
              } disabled:opacity-50`}
            >
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                inputMethod === 'text' ? 'bg-[#0F172A] text-white' : 'bg-[#F1F5F9] text-[#64748B]'
              }`}>
                <MessageSquare className="size-5" />
              </div>
              <span className="text-xs font-semibold text-[#0F172A]">{tDirect('inputText')}</span>
              <span className="text-[9px] text-[#94A3B8] text-center leading-tight">{tDirect('inputTextDesc')}</span>
            </button>
          </div>
        </div>

        {/* Start call button */}
        <div className="min-h-[52px] flex items-center md:justify-end">
          <button
            type="button"
            onClick={handleDirectStart}
            disabled={disabled}
            className="w-full md:w-auto md:min-w-[220px] flex items-center justify-center gap-2 rounded-2xl bg-[#0F172A] text-white px-5 py-3 text-sm font-medium transition-colors hover:bg-[#1E293B] disabled:opacity-40"
          >
            <Phone className="size-4" />
            {tDirect('startCall')}
          </button>
        </div>
      </div>
    </>
  );

  const renderAiAutoContent = () => (
    <>
      {/* Header */}
      <div className="text-center mb-6 max-w-2xl mx-auto">
        <h2 className="text-2xl font-bold text-[#0F172A] tracking-tight mb-1.5">
          {tAiAuto.rich('title', { accent: (chunks) => <span className="text-gradient">{chunks}</span> })}
        </h2>
        <p className="text-sm text-[#94A3B8]">{tAiAuto('subtitle')}</p>
      </div>

      {/* Quick action grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2.5 max-w-2xl mx-auto w-full mb-6">
        {QUICK_ACTIONS.map((action) => {
          const Icon = action.icon;
          return (
            <button
              key={`${action.scenarioType}-${action.subType}`}
              type="button"
              disabled={disabled}
              onClick={() => handleQuickAction(action)}
              className="glass-surface group flex flex-col items-center gap-2 p-4 rounded-2xl hover:border-white/90 hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)] active:scale-[0.99] transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="w-10 h-10 rounded-xl bg-[#F1F5F9] flex items-center justify-center group-hover:bg-[#E2E8F0] transition-colors">
                <Icon className="size-5 text-[#0F172A]" />
              </div>
              <span className="text-[11px] font-semibold text-[#0F172A] leading-tight text-center">
                {tQuick(action.labelKey)}
              </span>
              <span className="text-[9px] text-[#94A3B8] leading-tight text-center hidden sm:block">
                {tQuick(action.descKey)}
              </span>
            </button>
          );
        })}
      </div>

      {/* Divider + free text */}
      <div className="max-w-2xl mx-auto w-full">
        <div className="flex items-center gap-3 mb-3">
          <div className="flex-1 h-px bg-[#E2E8F0]" />
          <span className="text-[10px] text-[#CBD5E1] font-medium uppercase tracking-wider">
            {t('orFreeInput')}
          </span>
          <div className="flex-1 h-px bg-[#E2E8F0]" />
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder={t('freeInputPlaceholder')}
            className="flex-1 rounded-2xl border border-[#E2E8F0] px-4 py-2.5 text-sm text-[#334155] placeholder:text-[#CBD5E1] focus:outline-none focus:ring-1 focus:ring-[#0F172A] disabled:opacity-50"
          />
          <button
            type="button"
            onClick={handleFreeTextSubmit}
            disabled={!freeText.trim() || disabled}
            className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#0F172A] text-white transition-colors hover:bg-[#1E293B] disabled:opacity-30"
          >
            <Send className="size-4" />
          </button>
        </div>
      </div>
    </>
  );

  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  // Single unified return — fixed slot layout prevents Y-jump
  // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  return (
    <div className="flex flex-col h-full bg-transparent">
      <div className="flex-1 overflow-y-auto styled-scrollbar">
        <div className="px-4 md:px-8 pt-7 pb-8 flex flex-col items-center">
          {/* Back row */}
          <div className="w-full mb-3 min-h-6">
            <button
              type="button"
              onClick={handleBack}
              disabled={disabled || step === 'category'}
              className={`flex items-center gap-1 text-xs transition-colors ${
                step === 'category'
                  ? 'opacity-0 pointer-events-none'
                  : 'text-[#64748B] hover:text-[#334155]'
              }`}
            >
              <ChevronLeft className="size-3.5" />
              {step === 'direct' ? tCat('directTitle') : tCat('aiAutoTitle')}
            </button>
          </div>

          {/* Language pair — same Y position on all steps */}
          {renderLanguagePair()}

          {/* Step-specific content — only below changes */}
          {step === 'category' && renderCategoryContent()}
          {step === 'direct' && renderDirectContent()}
          {step === 'ai_auto' && renderAiAutoContent()}
        </div>
      </div>
    </div>
  );
}

export default ScenarioSelector;
