import { useState } from 'react';
import type { Reading } from '../data/cardTypes';
import { CardFace } from './CardFace';

export function Journal({ readings, onDelete }: { readings: Reading[]; onDelete: (id: string) => void }) {
  const [openId, setOpenId] = useState<string | null>(null);

  if (readings.length === 0) {
    return (
      <div>
        <h1>Journal</h1>
        <p>Nothing logged yet. Readings you save will collect here, so patterns can surface over time.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Journal</h1>
      <p className="muted">{readings.length} reading{readings.length === 1 ? '' : 's'} recorded</p>
      {readings.map((r) => {
        const open = openId === r.id;
        const date = new Date(r.date);
        return (
          <div className="panel journal-entry" key={r.id} onClick={() => setOpenId(open ? null : r.id)}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <h3 style={{ margin: 0 }}>{date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</h3>
              <span className="muted">{r.moonPhase}</span>
            </div>
            {r.question && <p style={{ fontStyle: 'italic' }}>"{r.question}"</p>}
            <div className="muted">{r.cards.map((c) => `${c.drawn.card.name}${c.drawn.reversed ? ' (R)' : ''}`).join(' · ')}</div>

            {open && (
              <div onClick={(e) => e.stopPropagation()}>
                <hr className="divider" />
                <div className="card-grid" style={{ gridTemplateColumns: `repeat(${Math.min(r.cards.length, 3)}, 1fr)` }}>
                  {r.cards.map((c) => (
                    <div className="card-slot" key={c.label}>
                      <span className="label">{c.label}</span>
                      <CardFace drawn={c.drawn} />
                    </div>
                  ))}
                </div>
                {r.aiSynthesis && <div className="reading-block">{r.aiSynthesis}</div>}
                {r.notes && (
                  <>
                    <h3>Notes</h3>
                    <p>{r.notes}</p>
                  </>
                )}
                <button className="btn btn-danger btn-block" onClick={() => onDelete(r.id)}>Delete Entry</button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
