import type { Card, DrawnCard } from './cardTypes';
import { majorArcana } from './majorArcana';
import { minorWands } from './minorWands';
import { minorCups } from './minorCups';
import { minorSwords } from './minorSwords';
import { minorPentacles } from './minorPentacles';

export const fullDeck: Card[] = [
  ...majorArcana,
  ...minorWands,
  ...minorCups,
  ...minorSwords,
  ...minorPentacles,
];

export function shuffledDeck(): Card[] {
  const deck = [...fullDeck];
  for (let i = deck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

export function drawCards(count: number, allowReversals: boolean, exclude: string[] = []): DrawnCard[] {
  const pool = shuffledDeck().filter((c) => !exclude.includes(c.id));
  return pool.slice(0, count).map((card) => ({
    card,
    reversed: allowReversals ? Math.random() < 0.35 : false,
  }));
}
