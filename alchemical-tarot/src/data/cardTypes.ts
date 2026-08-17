export type Suit = 'wands' | 'cups' | 'swords' | 'pentacles';

export interface Card {
  id: string;
  name: string;
  arcana: 'major' | 'minor';
  suit?: Suit;
  number: number;
  keywords: string[];
  upright: string;
  reversed: string;
  symbolism: string;
  astrology: string;
  glyph: string;
}

export interface DrawnCard {
  card: Card;
  reversed: boolean;
}

export interface Reading {
  id: string;
  date: string;
  moonPhase: string;
  question?: string;
  position: 'three-card' | 'single' | 'supporting';
  cards: { label: string; drawn: DrawnCard }[];
  notes?: string;
  aiSynthesis?: string;
}
