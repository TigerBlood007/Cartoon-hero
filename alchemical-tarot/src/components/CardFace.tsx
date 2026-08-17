import type { DrawnCard } from '../data/cardTypes';

const ROMAN = ['0', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X',
  'XI', 'XII', 'XIII', 'XIV', 'XV', 'XVI', 'XVII', 'XVIII', 'XIX', 'XX', 'XXI'];

export function CardBack({ onClick }: { onClick?: () => void }) {
  return (
    <div className="tarot-card back" onClick={onClick}>
      <span className="glyph">🜍</span>
    </div>
  );
}

export function CardFace({ drawn, onClick }: { drawn: DrawnCard; onClick?: () => void }) {
  const { card, reversed } = drawn;
  const numeral = card.arcana === 'major'
    ? ROMAN[card.number]
    : `${card.suit ? card.suit[0].toUpperCase() : ''}${card.number}`;
  return (
    <div className={`tarot-card${reversed ? ' reversed' : ''}`} onClick={onClick}>
      <div className="frame" />
      <span className="numeral">{numeral}</span>
      <span className="glyph">{card.glyph}</span>
      <span className="name">{card.name}</span>
    </div>
  );
}
