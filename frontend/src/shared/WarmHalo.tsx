import { ACCENT, GOLD, tint } from './styles';

interface WarmHaloProps {
  opacity?: number;
  className?: string;
}

export function WarmHalo({ opacity = 0.5, className }: WarmHaloProps) {
  return (
    <div className={className} style={{
      position: 'absolute', top: -260, left: '50%', transform: 'translateX(-50%)',
      width: 1100, height: 560, pointerEvents: 'none', opacity,
      background: `radial-gradient(ellipse at center, ${tint(ACCENT, 10)} 0%, ${tint(GOLD, 5)} 40%, transparent 70%)`,
      filter: 'blur(80px)',
    }} />
  );
}
