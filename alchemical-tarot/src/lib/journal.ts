import type { Reading } from '../data/cardTypes';

const STORAGE_KEY = 'alchemical-tarot-journal';

export function loadReadings(): Reading[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Reading[]) : [];
  } catch {
    return [];
  }
}

export function saveReading(reading: Reading): Reading[] {
  const all = loadReadings();
  all.unshift(reading);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  return all;
}

export function deleteReading(id: string): Reading[] {
  const all = loadReadings().filter((r) => r.id !== id);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  return all;
}

export function updateReadingNotes(id: string, notes: string): Reading[] {
  const all = loadReadings().map((r) => (r.id === id ? { ...r, notes } : r));
  localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  return all;
}
