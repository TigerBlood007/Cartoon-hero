import { useState } from 'react';
import type { Card } from '../data/cardTypes';
import { fullDeck } from '../data/deck';

export function CardPicker({ onPick }: { onPick: (card: Card, reversed: boolean) => void }) {
  const [query, setQuery] = useState('');

  const matches = query.trim()
    ? fullDeck.filter((c) => c.name.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8)
    : [];

  return (
    <div className="field">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search for the card you pulled…"
        autoFocus
      />
      {matches.length > 0 && (
        <div className="panel" style={{ marginTop: 8, padding: 6 }}>
          {matches.map((card) => (
            <div
              key={card.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 6px',
                borderBottom: '1px solid var(--border-faint)',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ color: 'var(--gold)' }}>{card.glyph}</span>
                {card.name}
              </span>
              <span style={{ display: 'flex', gap: 6 }}>
                <button className="btn" style={{ padding: '6px 10px', fontSize: '0.8rem' }} onClick={() => { onPick(card, false); setQuery(''); }}>
                  Upright
                </button>
                <button className="btn btn-ghost" style={{ padding: '6px 10px', fontSize: '0.8rem' }} onClick={() => { onPick(card, true); setQuery(''); }}>
                  Reversed
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
      {query.trim() && matches.length === 0 && <p className="muted">No card matches "{query}".</p>}
    </div>
  );
}
