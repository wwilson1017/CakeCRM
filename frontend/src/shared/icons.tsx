interface IconProps {
  size?: number;
  className?: string;
  strokeWidth?: number;
  style?: React.CSSProperties;
  /**
   * Hide a purely decorative icon from assistive tech — the usual case when the icon sits
   * inside a control that already carries its own `aria-label` or visible text.
   *
   * Declared and forwarded explicitly because `Ico` does not spread its rest props onto the
   * `<svg>`. TypeScript does not type-check hyphenated JSX attributes, so passing
   * `aria-hidden` to an icon that did not forward it compiled cleanly and was silently
   * dropped on the way to the DOM — a failure with no signal at all until a screen reader
   * hits it.
   */
  'aria-hidden'?: boolean | 'true' | 'false';
}

function Ico({ d, size = 18, strokeWidth = 1.75, fill = 'none', className, style, children, 'aria-hidden': ariaHidden }: IconProps & { d?: string; fill?: string; children?: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor"
         strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
         className={className} style={{ flexShrink: 0, ...style }} aria-hidden={ariaHidden}>
      {d && <path d={d} />}
      {children}
    </svg>
  );
}

export function IconPlus(p: IconProps) { return <Ico d="M12 5v14M5 12h14" {...p} />; }
export function IconSearch(p: IconProps) { return <Ico {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></Ico>; }
export function IconSettings(p: IconProps) { return <Ico {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></Ico>; }
export function IconArrowUp(p: IconProps) { return <Ico d="M12 19V5M5 12l7-7 7 7" {...p} />; }
export function IconArrowRight(p: IconProps) { return <Ico d="M5 12h14M13 5l7 7-7 7" {...p} />; }
export function IconArrowLeft(p: IconProps) { return <Ico d="M19 12H5M12 19l-7-7 7-7" {...p} />; }
export function IconMail(p: IconProps) { return <Ico {...p}><rect x="2" y="4" width="20" height="16" rx="2" /><path d="m22 7-10 6L2 7" /></Ico>; }
export function IconCalendar(p: IconProps) { return <Ico {...p}><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></Ico>; }
export function IconPhone(p: IconProps) { return <Ico d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.7a2 2 0 0 1-.5 2.1L7.9 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.5 2.7.6a2 2 0 0 1 1.7 2Z" {...p} />; }
export function IconUsers(p: IconProps) { return <Ico {...p}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8" /></Ico>; }
export function IconFunnel(p: IconProps) { return <Ico {...p}><path d="M22 3H2l8 9.5V19l4 2v-8.5L22 3z" /></Ico>; }
export function IconUser(p: IconProps) { return <Ico {...p}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></Ico>; }
export function IconDownload(p: IconProps) { return <Ico d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" {...p} />; }
export function IconBot({ size = 18, strokeWidth = 1.75, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" className={className} style={{ flexShrink: 0, ...style }}>
      <path d="M20 12a8 8 0 0 1-8 8 8 8 0 0 1-3.5-.8L4 20.5l1.3-4.5A8 8 0 0 1 4 12a8 8 0 0 1 16 0z" />
    </svg>
  );
}
export function IconSparkle(p: IconProps) { return <Ico d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8" {...p} />; }
export function IconChart(p: IconProps) { return <Ico d="M3 3v18h18M7 14v3M12 9v8M17 5v12" {...p} />; }
export function IconZap(p: IconProps) { return <Ico d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" {...p} />; }
export function IconCheck(p: IconProps) { return <Ico d="M20 6 9 17l-5-5" {...p} />; }
export function IconX(p: IconProps) { return <Ico d="M18 6 6 18M6 6l12 12" {...p} />; }
export function IconChevron(p: IconProps) { return <Ico d="m6 9 6 6 6-6" {...p} />; }
export function IconChevronRight(p: IconProps) { return <Ico d="m9 6 6 6-6 6" {...p} />; }
export function IconChevronLeft(p: IconProps) { return <Ico d="m15 6-6 6 6 6" {...p} />; }
export function IconDot(p: IconProps) { return <Ico {...p}><circle cx="12" cy="12" r="3" fill="currentColor" /></Ico>; }
// Filled by default (`fill="currentColor"`), unlike every stroked icon above: it marks a HOT
// deal (issue #125) beside two plain dots, and a hollow outline would read as one more ring
// rather than as a different thing. Added here rather than pulling in an icon package — the
// #73 precedent that added IconChevronLeft.
export function IconFlame(p: IconProps) { return <Ico d="M12 2c1.2 3 3 4.2 4.4 6A7.5 7.5 0 0 1 18 13a6 6 0 0 1-12 0c0-1.9.9-3.4 1.9-4.6.3 1 .9 1.8 1.8 2.2C10 8.4 10.6 4.9 12 2z" fill="currentColor" strokeWidth={1} {...p} />; }
export function IconMenu(p: IconProps) { return <Ico d="M4 6h16M4 12h16M4 18h16" {...p} />; }
export function IconMore(p: IconProps) { return <Ico {...p}><circle cx="5" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="19" cy="12" r="1.2" fill="currentColor" /></Ico>; }
export function IconAttach(p: IconProps) { return <Ico d="M21 12.5 12.5 21a5.5 5.5 0 0 1-7.8-7.8l9-9a3.7 3.7 0 0 1 5.2 5.2l-9 9a2 2 0 0 1-2.8-2.8L14.5 9" {...p} />; }
export function IconSend(p: IconProps) { return <Ico d="m22 2-7 20-4-9-9-4z M22 2 11 13" {...p} />; }
export function IconFilter(p: IconProps) { return <Ico d="M3 4h18l-7 9v7l-4-2v-5L3 4z" {...p} />; }
export function IconInbox(p: IconProps) { return <Ico {...p}><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-7a1.9 1.9 0 0 0-1.8-1h-9a1.9 1.9 0 0 0-1.8 1Z" /></Ico>; }
export function IconBook(p: IconProps) { return <Ico d="M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4V4zM4 16a4 4 0 0 1 4-4h12" {...p} />; }
export function IconFlag(p: IconProps) { return <Ico d="M4 22V4l8 3 8-3v12l-8 3-8-3v6" {...p} />; }
export function IconPause(p: IconProps) { return <Ico d="M6 4h4v16H6zM14 4h4v16h-4z" {...p} />; }
export function IconBrain(p: IconProps) { return <Ico {...p}><path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5v1a3 3 0 0 0 3 3h1V4H9zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 2 5 3 3 0 0 1-2 5v1a3 3 0 0 1-3 3h-1V4h1z" /></Ico>; }
export function IconGlobe(p: IconProps) { return <Ico {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z" /></Ico>; }
export function IconFile(p: IconProps) { return <Ico d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6" {...p} />; }
export function IconHome(p: IconProps) { return <Ico d="M3 12 12 3l9 9M5 10v10h14V10" {...p} />; }
export function IconLock(p: IconProps) { return <Ico {...p}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></Ico>; }
export function IconMoon(p: IconProps) { return <Ico d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" {...p} />; }
export function IconSun(p: IconProps) { return <Ico {...p}><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6 6l1.5 1.5M16.5 16.5 18 18M6 18l1.5-1.5M16.5 7.5 18 6" /></Ico>; }
export function IconCircle(p: IconProps) { return <Ico {...p}><circle cx="12" cy="12" r="9" /></Ico>; }
export function IconTarget(p: IconProps) { return <Ico {...p}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" fill="currentColor" /></Ico>; }
export function IconBuilding(p: IconProps) { return <Ico {...p}><rect x="4" y="3" width="16" height="18" rx="1" /><path d="M9 21v-4h6v4M8 7h2M14 7h2M8 11h2M14 11h2M8 15h2M14 15h2" /></Ico>; }
export function IconRefresh(p: IconProps) { return <Ico d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6" {...p} />; }
