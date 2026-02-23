'use client';

import { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SidebarMenuProps {
  icon: ReactNode;
  label: string;
  isCollapsed: boolean;
  isActive: boolean;
  onClick: () => void;
  badge?: number;
}

export default function SidebarMenu({
  icon,
  label,
  isCollapsed,
  isActive,
  onClick,
  badge,
}: SidebarMenuProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all duration-200',
        'hover:bg-white/45',
        isActive && 'bg-white/65 text-[#0B1324] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.88)]',
        !isActive && 'text-[#44546A]',
        isCollapsed && 'justify-center px-2'
      )}
    >
      <span className="shrink-0">{icon}</span>
      {!isCollapsed && (
        <>
          <span className="flex-1 text-left text-[13px] font-medium truncate">
            {label}
          </span>
          {badge !== undefined && badge > 0 && (
            <span className="shrink-0 bg-[#0B1324] text-white text-[10px] font-medium px-2 py-0.5 rounded-full">
              {badge > 99 ? '99+' : badge}
            </span>
          )}
        </>
      )}
    </button>
  );
}
