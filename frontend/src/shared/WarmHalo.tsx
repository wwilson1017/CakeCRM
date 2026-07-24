interface WarmHaloProps {
  opacity?: number;
  className?: string;
}

export function WarmHalo({ opacity = 0.5, className }: WarmHaloProps) {
  return (
    <div className={className} style={{
      position: 'absolute', top: -260, left: '50%', transform: 'translateX(-50%)',
      width: 1100, height: 560, pointerEvents: 'none', opacity,
      background: 'radial-gradient(ellipse at center, rgba(176,58,82,0.10) 0%, rgba(176,124,46,0.05) 40%, transparent 70%)',
      filter: 'blur(80px)',
    }} />
  );
}
