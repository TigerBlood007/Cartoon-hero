import type { DrawnCard } from '../data/cardTypes';
import { CardFace } from './CardFace';

export function CardDetail({ drawn, onClose }: { drawn: DrawnCard; onClose: () => void }) {
  const { card, reversed } = drawn;
  return (
    <div className="panel">
      <div style={{ display: 'flex', gap: 14 }}>
        <div style={{ width: 110, flexShrink: 0 }}>
          <CardFace drawn={drawn} />
        </div>
        <div>
          <div className="eyebrow">{card.arcana === 'major' ? 'Major Arcana' : card.suit}</div>
          <h2>{card.name}{reversed ? ' (Reversed)' : ''}</h2>
          <div>
            {card.keywords.map((k) => <span key={k} className="tag">{k}</span>)}
          </div>
        </div>
      </div>
      <hr className="divider" />
      <h3>{reversed ? 'Reversed' : 'Upright'}</h3>
      <p>{reversed ? card.reversed : card.upright}</p>
      <h3>Symbolism</h3>
      <p>{card.symbolism}</p>
      <p className="muted">Astrological correspondence: {card.astrology}</p>
      <button className="btn btn-ghost btn-block" onClick={onClose}>Close</button>
    </div>
  );
}
