export type View = 'draw' | 'journal' | 'astrology' | 'settings';

const ITEMS: { view: View; icon: string; label: string }[] = [
  { view: 'draw', icon: '🃏', label: 'Draw' },
  { view: 'journal', icon: '📖', label: 'Journal' },
  { view: 'astrology', icon: '☽', label: 'Astrology' },
  { view: 'settings', icon: '⚙︎', label: 'Settings' },
];

export function Nav({ current, onChange }: { current: View; onChange: (v: View) => void }) {
  return (
    <nav className="nav">
      {ITEMS.map((item) => (
        <button
          key={item.view}
          className={`nav-btn${current === item.view ? ' active' : ''}`}
          onClick={() => onChange(item.view)}
        >
          <span className="icon">{item.icon}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
