const KEY_STORAGE = 'alchemical-tarot-anthropic-key';
const BIRTH_STORAGE = 'alchemical-tarot-birth-profile';
const NOTIFY_STORAGE = 'alchemical-tarot-notify-hour';

export function getApiKey(): string {
  return localStorage.getItem(KEY_STORAGE) ?? '';
}

export function setApiKey(key: string) {
  if (key) localStorage.setItem(KEY_STORAGE, key);
  else localStorage.removeItem(KEY_STORAGE);
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
