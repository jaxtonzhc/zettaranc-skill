interface Verdict {
  state: string;
  reason: string;
  conditions: string[];
  action: string;
}

const STYLES: Record<string, { wrap: string; badge: string; label: string }> = {
  '可买': {
    wrap: 'border-accent-green/40 bg-accent-green/10 shadow-[0_0_30px_-12px_rgba(34,197,94,0.5)]',
    badge: 'bg-accent-green text-white',
    label: '可买',
  },
  '待确认': {
    wrap: 'border-accent-gold/40 bg-accent-gold/10 shadow-[0_0_30px_-12px_rgba(245,158,11,0.5)]',
    badge: 'bg-accent-gold text-white',
    label: '待确认',
  },
  '不买': {
    wrap: 'border-accent-red/40 bg-accent-red/10 shadow-[0_0_30px_-12px_rgba(239,68,68,0.5)]',
    badge: 'bg-accent-red text-white',
    label: '不买',
  },
};

const DEFAULT_STYLE = {
  wrap: 'border-border/40 bg-bg-hover/50',
  badge: 'bg-bg-hover text-text-secondary border border-border/50',
  label: '观望',
};

export default function VerdictBanner({ verdict }: { verdict: Verdict }) {
  const style = STYLES[verdict.state] || DEFAULT_STYLE;
  const label = verdict.state || '观望';

  return (
    <div className={`rounded-2xl border px-5 py-4 ${style.wrap}`}>
      <div className="flex flex-wrap items-center gap-3">
        <span className={`text-xs font-black px-3 py-1.5 rounded-lg tracking-widest ${style.badge}`}>
          {label}
        </span>
        <div className="flex-1 min-w-[200px]">
          <div className="text-sm font-bold text-text-primary">{verdict.reason || '暂无结论'}</div>
          <div className="text-xs text-text-secondary mt-1">
            {verdict.action} {verdict.conditions?.length ? `· 条件：${verdict.conditions.join('；')}` : ''}
          </div>
        </div>
      </div>
    </div>
  );
}
