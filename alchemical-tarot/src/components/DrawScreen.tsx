import { useState } from 'react';
import type { DrawnCard, Reading } from '../data/cardTypes';
import { drawCards } from '../data/deck';
import { CardBack, CardFace } from './CardFace';
import { CardDetail } from './CardDetail';
import type { MoonPhaseInfo } from '../lib/astro';
import { synthesizeReading } from '../lib/ai';

type SpreadType = 'single' | 'three-card';

const LABELS_THREE = ['Past', 'Present', 'Future'];

export function DrawScreen({
  moon,
  apiKey,
  onSave,
}: {
  moon: MoonPhaseInfo;
  apiKey: string;
  onSave: (reading: Reading) => void;
}) {
  const [spreadType, setSpreadType] = useState<SpreadType>('three-card');
  const [allowReversals, setAllowReversals] = useState(true);
  const [question, setQuestion] = useState('');
  const [drawn, setDrawn] = useState<{ label: string; drawn: DrawnCard }[] | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [supporting, setSupporting] = useState<{ label: string; drawn: DrawnCard }[]>([]);
  const [notes, setNotes] = useState('');
  const [aiText, setAiText] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [saved, setSaved] = useState(false);

  function beginDraw() {
    const count = spreadType === 'single' ? 1 : 3;
    const labels = spreadType === 'single' ? ['Reading'] : LABELS_THREE;
    const cards = drawCards(count, allowReversals);
    setDrawn(labels.map((label, i) => ({ label, drawn: cards[i] })));
    setRevealed(new Set());
    setSupporting([]);
    setNotes('');
    setAiText('');
    setAiError('');
    setSaved(false);
    setSelectedIndex(null);
  }

  function reveal(i: number) {
    setRevealed((prev) => new Set(prev).add(i));
  }

  function pullSupporting() {
    if (!drawn) return;
    const usedIds = [...drawn, ...supporting].map((d) => d.drawn.card.id);
    const [card] = drawCards(1, allowReversals, usedIds);
    setSupporting((prev) => [...prev, { label: `Supporting ${prev.length + 1}`, drawn: card }]);
  }

  async function runAiSynthesis() {
    if (!drawn || !apiKey) return;
    setAiLoading(true);
    setAiError('');
    try {
      const text = await synthesizeReading(apiKey, {
        cards: [...drawn, ...supporting],
        question: question || undefined,
        moon,
      });
      setAiText(text);
    } catch (e: any) {
      setAiError(e.message ?? 'Something went wrong reaching the AI.');
    } finally {
      setAiLoading(false);
    }
  }

  function saveToJournal() {
    if (!drawn) return;
    const reading: Reading = {
      id: crypto.randomUUID(),
      date: new Date().toISOString(),
      moonPhase: moon.name,
      question: question || undefined,
      position: spreadType,
      cards: [...drawn, ...supporting],
      notes: notes || undefined,
      aiSynthesis: aiText || undefined,
    };
    onSave(reading);
    setSaved(true);
  }

  const allCards = drawn ? [...drawn, ...supporting] : [];
  const allRevealed = drawn ? drawn.every((_, i) => revealed.has(i)) : false;

  return (
    <div>
      <div className="eyebrow">Clear the space</div>
      <h1>Draw the Cards</h1>
      <p>
        Sit with it a moment before you shuffle. {moon.glyph} Tonight the moon is a{' '}
        {moon.name.toLowerCase()} — let that color what you're asking.
      </p>

      {!drawn && (
        <div className="panel">
          <div className="field">
            <label>Spread</label>
            <div className="spread-choice">
              <button
                className={`chip${spreadType === 'three-card' ? ' selected' : ''}`}
                onClick={() => setSpreadType('three-card')}
              >
                Past · Present · Future
              </button>
              <button
                className={`chip${spreadType === 'single' ? ' selected' : ''}`}
                onClick={() => setSpreadType('single')}
              >
                Single Card
              </button>
            </div>
          </div>
          <div className="field">
            <label>What's on your mind (optional)</label>
            <textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Leave blank for a general reading" />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.85rem', color: 'var(--ink-dim)' }}>
            <input type="checkbox" checked={allowReversals} onChange={(e) => setAllowReversals(e.target.checked)} />
            Allow reversed cards
          </label>
          <button className="btn btn-primary btn-block" style={{ marginTop: 14 }} onClick={beginDraw}>
            Shuffle &amp; Draw
          </button>
        </div>
      )}

      {drawn && (
        <>
          <div className="card-grid">
            {drawn.map((entry, i) => (
              <div className="card-slot" key={entry.label}>
                <span className="label">{entry.label}</span>
                {revealed.has(i) ? (
                  <CardFace drawn={entry.drawn} onClick={() => setSelectedIndex(i)} />
                ) : (
                  <CardBack onClick={() => reveal(i)} />
                )}
                {revealed.has(i) && (
                  <span className="caption">
                    {entry.drawn.card.name}{entry.drawn.reversed ? ' (R)' : ''}
                  </span>
                )}
              </div>
            ))}
          </div>

          {supporting.length > 0 && (
            <div className="card-grid" style={{ gridTemplateColumns: `repeat(${supporting.length}, 1fr)` }}>
              {supporting.map((entry, i) => (
                <div className="card-slot" key={entry.label}>
                  <span className="label">{entry.label}</span>
                  <CardFace drawn={entry.drawn} onClick={() => setSelectedIndex(drawn.length + i)} />
                  <span className="caption">{entry.drawn.card.name}{entry.drawn.reversed ? ' (R)' : ''}</span>
                </div>
              ))}
            </div>
          )}

          {selectedIndex !== null && allCards[selectedIndex] && (
            <CardDetail drawn={allCards[selectedIndex].drawn} onClose={() => setSelectedIndex(null)} />
          )}

          {allRevealed && (
            <div className="panel">
              <button className="btn btn-ghost btn-block" onClick={pullSupporting}>
                + Pull a supporting card (if it's not clear yet)
              </button>

              <hr className="divider" />

              {apiKey ? (
                <>
                  {!aiText && (
                    <button className="btn btn-block" onClick={runAiSynthesis} disabled={aiLoading}>
                      {aiLoading ? 'Weaving the reading…' : '✦ Synthesize a reading'}
                    </button>
                  )}
                  {aiError && <p style={{ color: 'var(--danger)' }}>{aiError}</p>}
                  {aiText && (
                    <div className="reading-block">{aiText}</div>
                  )}
                </>
              ) : (
                <p className="muted">
                  Add an Anthropic API key in Settings to generate a synthesized reading that
                  weaves these cards together — otherwise, tap each card above for its traditional meaning.
                </p>
              )}

              <hr className="divider" />

              <div className="field">
                <label>Notes</label>
                <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What did this bring up?" />
              </div>

              <button className="btn btn-primary btn-block" onClick={saveToJournal} disabled={saved}>
                {saved ? 'Saved to Journal ✓' : 'Save to Journal'}
              </button>
              <button className="btn btn-ghost btn-block" style={{ marginTop: 8 }} onClick={() => setDrawn(null)}>
                New Reading
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
