import { useState } from 'react';
import type { Card } from '../data/cardTypes';
import { majorArcana } from '../data/majorArcana';
import { minorWands } from '../data/minorWands';
import { minorCups } from '../data/minorCups';
import { minorSwords } from '../data/minorSwords';
import { minorPentacles } from '../data/minorPentacles';
import { CardDetail } from './CardDetail';

const SECTIONS: { title: string; cards: Card[] }[] = [
  { title: 'Major Arcana', cards: majorArcana },
  { title: 'Wands', cards: minorWands },
  { title: 'Cups', cards: minorCups },
  { title: 'Swords', cards: minorSwords },
  { title: 'Pentacles', cards: minorPentacles },
];

export function Library() {
  const [query, setQuery] = useState('');
  const [openCard, setOpenCard] = useState<Card | null>(null);

  const q = query.trim().toLowerCase();
  const visibleSections = SECTIONS.map((section) => ({
    ...section,
    cards: q
      ? section.cards.filter((c) => c.name.toLowerCase().includes(q) || c.keywords.some((k) => k.toLowerCase().includes(q)))
      : section.cards,
  })).filter((section) => section.cards.length > 0);

  return (
    <div>
      <h1>Library</h1>
      <p>Every card's full meaning, on hand any time — not just when it's drawn.</p>
      <div className="field">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or keyword…"
        />
      </div>

      {openCard && (
        <CardDetail drawn={{ card: openCard, reversed: false }} onClose={() => setOpenCard(null)} />
      )}

      {visibleSections.map((section) => (
        <div key={section.title}>
          <h2>{section.title}</h2>
          <div className="panel" style={{ padding: 6 }}>
            {section.cards.map((card) => (
              <button
                key={card.id}
                onClick={() => setOpenCard(card)}
                className="btn btn-ghost"
                style={{
                  width: '100%',
                  justifyContent: 'flex-start',
                  gap: 10,
                  border: 'none',
                  borderBottom: '1px solid var(--border-faint)',
                  borderRadius: 0,
                  padding: '10px 8px',
                }}
              >
                <span style={{ color: 'var(--gold)', width: 20 }}>{card.glyph}</span>
                {card.name}
              </button>
            ))}
          </div>
        </div>
      ))}

      {visibleSections.length === 0 && <p className="muted">No cards match "{query}".</p>}
    </div>
  );
}
