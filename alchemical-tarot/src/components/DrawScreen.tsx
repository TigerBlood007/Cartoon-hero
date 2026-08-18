import { useState } from 'react';
import type { Card, DrawnCard, Reading } from '../data/cardTypes';
import { drawCards } from '../data/deck';
import { CardBack, CardFace } from './CardFace';
import { CardDetail } from './CardDetail';
import { CardPicker } from './CardPicker';
import type { MoonPhaseInfo } from '../lib/astro';
import { synthesizeReading } from '../lib/ai';
import type { AiModel } from '../lib/settings';

type SpreadType = 'single' | 'three-card';
type Mode = 'digital' | 'physical';
type Slot = { label: string; drawn: DrawnCard | null };

const LABELS_THREE = ['Past', 'Present', 'Future'];

export function DrawScreen({
  moon,
  apiKey,
  aiModel,
  onSave,
}: {
  moon: MoonPhaseInfo;
  apiKey: string;
  aiModel: AiModel;
  onSave: (reading: Reading) => void;
}) {
  const [mode, setMode] = useState<Mode>('digital');
  const [spreadType, setSpreadType] = useState<SpreadType>('three-card');
  const [allowReversals, setAllowReversals] = useState(true);
  const [question, setQuestion] = useState('');
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<{ list: 'main' | 'supporting'; index: number } | null>(null);
  const [supporting, setSupporting] = useState<Slot[]>([]);
  const [pickingSupporting, setPickingSupporting] = useState(false);
  const [notes, setNotes] = useState('');
  const [aiText, setAiText] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const [saved, setSaved] = useState(false);

  function resetFor(newMode: Mode) {
    setMode(newMode);
  }

  function begin() {
    const labels = spreadType === 'single' ? ['Reading'] : LABELS_THREE;
    if (mode === 'digital') {
      const cards = drawCards(labels.length, allowReversals);
      setSlots(labels.map((label, i) => ({ label, drawn: cards[i] })));
      setRevealed(new Set());
    } else {
      setSlots(labels.map((label) => ({ label, drawn: null })));
    }
    setSupporting([]);
    setNotes('');
    setAiText('');
    setAiError('');
    setSaved(false);
    setSelected(null);
    setPickingSupporting(false);
  }

  function reveal(i: number) {
    setRevealed((prev) => new Set(prev).add(i));
  }

  function fillNextSlot(card: Card, reversed: boolean) {
    if (!slots) return;
    const nextIndex = slots.findIndex((s) => s.drawn === null);
    if (nextIndex === -1) return;
    const next = [...slots];
    next[nextIndex] = { ...next[nextIndex], drawn: { card, reversed } };
    setSlots(next);
  }

  function pullSupportingDigital() {
    if (!slots) return;
    const usedIds = [...slots, ...supporting].filter((s) => s.drawn).map((s) => s.drawn!.card.id);
    const [card] = drawCards(1, allowReversals, usedIds);
    setSupporting((prev) => [...prev, { label: `Supporting ${prev.length + 1}`, drawn: card }]);
  }

  function addSupportingPhysical(card: Card, reversed: boolean) {
    setSupporting((prev) => [...prev, { label: `Supporting ${prev.length + 1}`, drawn: { card, reversed } }]);
    setPickingSupporting(false);
  }

  async function runAiSynthesis() {
    if (!slots || !apiKey) return;
    const filled = [...slots, ...supporting].filter((s): s is { label: string; drawn: DrawnCard } => s.drawn !== null);
    setAiLoading(true);
    setAiError('');
    try {
      const text = await synthesizeReading(apiKey, {
        cards: filled,
        question: question || undefined,
        moon,
        model: aiModel,
      });
      setAiText(text);
    } catch (e: any) {
      setAiError(e.message ?? 'Something went wrong reaching the AI.');
    } finally {
      setAiLoading(false);
    }
  }

  function saveToJournal() {
    if (!slots) return;
    const filled = [...slots, ...supporting].filter((s): s is { label: string; drawn: DrawnCard } => s.drawn !== null);
    const reading: Reading = {
      id: crypto.randomUUID(),
      date: new Date().toISOString(),
      moonPhase: moon.name,
      question: question || undefined,
      position: spreadType,
      source: mode,
      cards: filled,
      notes: notes || undefined,
      aiSynthesis: aiText || undefined,
    };
    onSave(reading);
    setSaved(true);
  }

  const allFilled = slots ? slots.every((s) => s.drawn !== null) : false;
  const allRevealed = mode === 'physical' ? allFilled : slots ? slots.every((_, i) => revealed.has(i)) : false;
  const selectedDrawn =
    selected && slots
      ? (selected.list === 'main' ? slots[selected.index]?.drawn : supporting[selected.index]?.drawn)
      : null;

  return (
    <div>
      <div className="eyebrow">Clear the space</div>
      <h1>Draw the Cards</h1>
      <p>
        Sit with it a moment before you shuffle. {moon.glyph} Tonight the moon is a{' '}
        {moon.name.toLowerCase()} — let that color what you're asking.
      </p>

      {!slots && (
        <div className="panel">
          <div className="field">
            <label>How were these cards pulled?</label>
            <div className="spread-choice">
              <button className={`chip${mode === 'digital' ? ' selected' : ''}`} onClick={() => resetFor('digital')}>
                Shuffle in the app
              </button>
              <button className={`chip${mode === 'physical' ? ' selected' : ''}`} onClick={() => resetFor('physical')}>
                Record a real-life pull
              </button>
            </div>
          </div>
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
          {mode === 'digital' && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.85rem', color: 'var(--ink-dim)' }}>
              <input type="checkbox" checked={allowReversals} onChange={(e) => setAllowReversals(e.target.checked)} />
              Allow reversed cards
            </label>
          )}
          <button className="btn btn-primary btn-block" style={{ marginTop: 14 }} onClick={begin}>
            {mode === 'digital' ? 'Shuffle & Draw' : 'Start Recording'}
          </button>
        </div>
      )}

      {slots && (
        <>
          <div className="card-grid">
            {slots.map((entry, i) => (
              <div className="card-slot" key={entry.label}>
                <span className="label">{entry.label}</span>
                {entry.drawn ? (
                  mode === 'digital' && !revealed.has(i) ? (
                    <CardBack onClick={() => reveal(i)} />
                  ) : (
                    <CardFace drawn={entry.drawn} onClick={() => setSelected({ list: 'main', index: i })} />
                  )
                ) : (
                  <div className="tarot-card back" style={{ opacity: 0.4 }}>
                    <span className="glyph">?</span>
                  </div>
                )}
                {entry.drawn && (mode === 'physical' || revealed.has(i)) && (
                  <span className="caption">{entry.drawn.card.name}{entry.drawn.reversed ? ' (R)' : ''}</span>
                )}
              </div>
            ))}
          </div>

          {mode === 'physical' && !allFilled && (
            <div className="panel">
              <p className="muted">Which card did you pull for <strong>{slots.find((s) => s.drawn === null)?.label}</strong>?</p>
              <CardPicker onPick={fillNextSlot} />
            </div>
          )}

          {supporting.length > 0 && (
            <div className="card-grid" style={{ gridTemplateColumns: `repeat(${supporting.length}, 1fr)` }}>
              {supporting.map((entry, i) =>
                entry.drawn ? (
                  <div className="card-slot" key={entry.label}>
                    <span className="label">{entry.label}</span>
                    <CardFace drawn={entry.drawn} onClick={() => setSelected({ list: 'supporting', index: i })} />
                    <span className="caption">{entry.drawn.card.name}{entry.drawn.reversed ? ' (R)' : ''}</span>
                  </div>
                ) : null,
              )}
            </div>
          )}

          {selectedDrawn && (
            <CardDetail drawn={selectedDrawn} onClose={() => setSelected(null)} />
          )}

          {allRevealed && allFilled && (
            <div className="panel">
              {mode === 'digital' ? (
                <button className="btn btn-ghost btn-block" onClick={pullSupportingDigital}>
                  + Pull a supporting card (if it's not clear yet)
                </button>
              ) : pickingSupporting ? (
                <CardPicker onPick={addSupportingPhysical} />
              ) : (
                <button className="btn btn-ghost btn-block" onClick={() => setPickingSupporting(true)}>
                  + Add a supporting card (if you pulled one)
                </button>
              )}

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
              <button className="btn btn-ghost btn-block" style={{ marginTop: 8 }} onClick={() => setSlots(null)}>
                New Reading
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
