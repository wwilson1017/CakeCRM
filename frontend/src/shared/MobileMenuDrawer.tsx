import type { ComponentType, ReactNode } from 'react';
import { IconChart, IconUsers, IconBuilding, IconFunnel, IconCheck, IconCalendar, IconSettings, IconSparkle, IconLock } from './icons';
import { BG_CARD, INK, INK_MUTE, LINE, ACCENT, FONT_DISPLAY } from './styles';
import { ThemeToggle } from '../crm/components/ThemeToggle';

interface MobileMenuDrawerProps {
  onClose: () => void;
  navigate: (path: string) => void;
  onSignOut?: () => void;
  children?: ReactNode;
}

interface NavItem {
  icon: ComponentType<{ size?: number; strokeWidth?: number }>;
  label: string;
  action: () => void;
}

export function MobileMenuDrawer({ onClose, navigate, onSignOut, children }: MobileMenuDrawerProps) {
  const go = (path: string) => () => { onClose(); navigate(path); };
  const items: NavItem[] = [
    { icon: IconChart, label: 'Dashboard', action: go('/crm') },
    { icon: IconFunnel, label: 'Pipeline', action: go('/crm/pipeline') },
    { icon: IconUsers, label: 'Contacts', action: go('/crm/contacts') },
    { icon: IconBuilding, label: 'Companies', action: go('/crm/companies') },
    { icon: IconCheck, label: 'Tasks', action: go('/crm/tasks') },
    { icon: IconCalendar, label: 'Reminders', action: go('/crm/reminders') },
    { icon: IconSettings, label: 'Settings', action: go('/crm/settings') },
    { icon: IconSparkle, label: 'AI Setup', action: go('/setup') },
  ];
  if (onSignOut) {
    items.push({ icon: IconLock, label: 'Sign out', action: () => { onClose(); onSignOut(); } });
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 50,
      background: BG_CARD,
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 16px', borderBottom: `1px solid ${LINE}`,
      }}>
        <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: INK }}>Menu</span>
        <div
          onClick={onClose}
          style={{ cursor: 'pointer', color: INK_MUTE, fontSize: 22, padding: '0 4px' }}
        >&times;</div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '12px 16px' }}>
        {items.map(item => (
          <div
            key={item.label}
            onClick={item.action}
            style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '12px 8px', borderRadius: 4, cursor: 'pointer',
              color: INK,
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = ACCENT; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = INK; }}
          >
            <item.icon size={18} strokeWidth={1.85} />
            <span style={{ fontSize: 14 }}>{item.label}</span>
          </div>
        ))}
        <div style={{ marginTop: 8 }}>
          <ThemeToggle full />
        </div>
      </div>
      {children}
    </div>
  );
}
