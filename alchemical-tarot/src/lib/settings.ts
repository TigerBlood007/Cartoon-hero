const KEY_STORAGE = 'alchemical-tarot-anthropic-key';
const MODEL_STORAGE = 'alchemical-tarot-anthropic-model';
const BIRTH_STORAGE = 'alchemical-tarot-birth-profile';
const NOTIFY_STORAGE = 'alchemical-tarot-notify-hour';

export function getApiKey(): string {
  return localStorage.getItem(KEY_STORAGE) ?? '';
}

export function setApiKey(key: string) {
  if (key) localStorage.setItem(KEY_STORAGE, key);
  else localStorage.removeItem(KEY_STORAGE);
}

export type AiModel = 'claude-haiku-4-5' | 'claude-sonnet-5' | 'claude-opus-5';
export const DEFAULT_AI_MODEL: AiModel = 'claude-sonnet-5';

export function getAiModel(): AiModel {
  const stored = localStorage.getItem(MODEL_STORAGE);
  if (stored === 'claude-haiku-4-5' || stored === 'claude-sonnet-5' || stored === 'claude-opus-5') {
    return stored;
  }
  return DEFAULT_AI_MODEL;
}

export function setAiModel(model: AiModel) {
  localStorage.setItem(MODEL_STORAGE, model);
}

export interface BirthProfile {
  date: string;
  time: string;
  utcOffsetHours: number;
  latitude: number | null;
  longitude: number | null;
  placeName: string;
}

export function getBirthProfile(): BirthProfile | null {
  try {
    const raw = localStorage.getItem(BIRTH_STORAGE);
    return raw ? (JSON.parse(raw) as BirthProfile) : null;
  } catch {
    return null;
  }
}

export function setBirthProfile(profile: BirthProfile) {
  localStorage.setItem(BIRTH_STORAGE, JSON.stringify(profile));
}

export function getNotifyHour(): number | null {
  const raw = localStorage.getItem(NOTIFY_STORAGE);
  return raw ? Number(raw) : null;
}

export function setNotifyHour(hour: number | null) {
  if (hour === null) localStorage.removeItem(NOTIFY_STORAGE);
  else localStorage.setItem(NOTIFY_STORAGE, String(hour));
}
