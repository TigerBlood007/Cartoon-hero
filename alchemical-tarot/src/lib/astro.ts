import { Body, GeoVector, Ecliptic, EclipticGeoMoon, SunPosition, MoonPhase, SiderealTime, MakeTime } from 'astronomy-engine';

const ZODIAC_SIGNS = [
  'Aries', 'Taurus', 'Gemini', 'Cancer', 'Leo', 'Virgo',
  'Libra', 'Scorpio', 'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces',
];

const ZODIAC_GLYPHS: Record<string, string> = {
  Aries: '♈', Taurus: '♉', Gemini: '♊', Cancer: '♋', Leo: '♌', Virgo: '♍',
  Libra: '♎', Scorpio: '♏', Sagittarius: '♐', Capricorn: '♑', Aquarius: '♒', Pisces: '♓',
};

export interface ZodiacPosition {
  longitude: number;
  sign: string;
  glyph: string;
  degreeInSign: number;
}

export interface MoonPhaseInfo {
  phaseAngle: number;
  name: string;
  illuminationFraction: number;
  glyph: string;
}

export interface NatalChart {
  sun: ZodiacPosition;
  moon: ZodiacPosition;
  ascendant: ZodiacPosition | null;
  mercury: ZodiacPosition;
  venus: ZodiacPosition;
  mars: ZodiacPosition;
  jupiter: ZodiacPosition;
  saturn: ZodiacPosition;
  uranus: ZodiacPosition;
  neptune: ZodiacPosition;
  pluto: ZodiacPosition;
}

export interface BirthInput {
  date: string; // yyyy-mm-dd
  time?: string; // HH:mm, local
  utcOffsetHours: number; // e.g. -5 for EST
  latitude?: number;
  longitude?: number;
}

export interface GeocodeResult {
  name: string;
  admin1?: string;
  country: string;
  latitude: number;
  longitude: number;
  timezone: string;
}

// Geocentric apparent ecliptic longitude — the "as seen from Earth" position
// astrology actually uses, as opposed to astronomy-engine's EclipticLongitude
// which is heliocentric (as seen from the Sun) and doesn't apply to the Sun itself.
function geocentricLongitude(body: Body, date: ReturnType<typeof MakeTime>): number {
  if (body === Body.Sun) return SunPosition(date).elon;
  if (body === Body.Moon) return EclipticGeoMoon(date).lon;
  return Ecliptic(GeoVector(body, date, true)).elon;
}

function toZodiacPosition(eclipticLongitude: number): ZodiacPosition {
  const lon = ((eclipticLongitude % 360) + 360) % 360;
  const signIndex = Math.floor(lon / 30);
  const sign = ZODIAC_SIGNS[signIndex];
  return {
    longitude: lon,
    sign,
    glyph: ZODIAC_GLYPHS[sign],
    degreeInSign: lon - signIndex * 30,
  };
}

export function getMoonPhase(date: Date = new Date()): MoonPhaseInfo {
  const angle = MoonPhase(date);
  const illuminationFraction = (1 - Math.cos((angle * Math.PI) / 180)) / 2;
  let name: string;
  let glyph: string;
  if (angle < 11.25) { name = 'New Moon'; glyph = '🌑'; }
  else if (angle < 78.75) { name = 'Waxing Crescent'; glyph = '🌒'; }
  else if (angle < 101.25) { name = 'First Quarter'; glyph = '🌓'; }
  else if (angle < 168.75) { name = 'Waxing Gibbous'; glyph = '🌔'; }
  else if (angle < 191.25) { name = 'Full Moon'; glyph = '🌕'; }
  else if (angle < 258.75) { name = 'Waning Gibbous'; glyph = '🌖'; }
  else if (angle < 281.25) { name = 'Last Quarter'; glyph = '🌗'; }
  else if (angle < 348.75) { name = 'Waning Crescent'; glyph = '🌘'; }
  else { name = 'New Moon'; glyph = '🌑'; }
  return { phaseAngle: angle, name, illuminationFraction, glyph };
}

// Approximate mean obliquity of the ecliptic (IAU formula), accurate to
// arcsecond level across many centuries either side of J2000 — plenty for
// an ascendant calculation.
function obliquityOfEcliptic(date: Date): number {
  const T = (date.getTime() / 86400000 + 2440587.5 - 2451545.0) / 36525;
  return 23.4392911 - 0.0130042 * T - 0.00000016 * T * T + 0.000000504 * T * T * T;
}

function computeAscendant(date: Date, latitude: number, longitude: number): number {
  const gastHours = SiderealTime(date); // Greenwich apparent sidereal time, in hours
  const lstHours = ((gastHours + longitude / 15) % 24 + 24) % 24;
  const ramc = lstHours * 15; // Right ascension of the midheaven, degrees
  const eps = (obliquityOfEcliptic(date) * Math.PI) / 180;
  const ramcRad = (ramc * Math.PI) / 180;
  const latRad = (latitude * Math.PI) / 180;

  const y = -Math.cos(ramcRad);
  const x = Math.sin(eps) * Math.tan(latRad) + Math.cos(eps) * Math.sin(ramcRad);
  let asc = (Math.atan2(y, x) * 180) / Math.PI;
  asc = ((asc % 360) + 360) % 360;
  return asc;
}

export function buildNatalChart(birth: BirthInput): NatalChart {
  const timeStr = birth.time && birth.time.length > 0 ? birth.time : '12:00';
  const [h, m] = timeStr.split(':').map(Number);
  const localDate = new Date(`${birth.date}T00:00:00Z`);
  const utcMillis = localDate.getTime() + ((h + m / 60 - birth.utcOffsetHours) * 3600000);
  const date = new Date(utcMillis);
  const astroTime = MakeTime(date);

  const sun = toZodiacPosition(geocentricLongitude(Body.Sun, astroTime));
  const moon = toZodiacPosition(geocentricLongitude(Body.Moon, astroTime));
  const mercury = toZodiacPosition(geocentricLongitude(Body.Mercury, astroTime));
  const venus = toZodiacPosition(geocentricLongitude(Body.Venus, astroTime));
  const mars = toZodiacPosition(geocentricLongitude(Body.Mars, astroTime));
  const jupiter = toZodiacPosition(geocentricLongitude(Body.Jupiter, astroTime));
  const saturn = toZodiacPosition(geocentricLongitude(Body.Saturn, astroTime));
  const uranus = toZodiacPosition(geocentricLongitude(Body.Uranus, astroTime));
  const neptune = toZodiacPosition(geocentricLongitude(Body.Neptune, astroTime));
  const pluto = toZodiacPosition(geocentricLongitude(Body.Pluto, astroTime));

  let ascendant: ZodiacPosition | null = null;
  if (birth.time && birth.latitude !== undefined && birth.longitude !== undefined) {
    ascendant = toZodiacPosition(computeAscendant(date, birth.latitude, birth.longitude));
  }

  return { sun, moon, ascendant, mercury, venus, mars, jupiter, saturn, uranus, neptune, pluto };
}

// Free, keyless geocoding lookup so she can type a birthplace name instead
// of hunting down exact coordinates. Falls back gracefully — the caller
// should let manual lat/long entry work offline regardless.
export async function lookupBirthplace(query: string): Promise<GeocodeResult[]> {
  const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=8&language=en&format=json`;
  const res = await fetch(url);
  if (!res.ok) throw new Error('Lookup failed');
  const data = await res.json();
  if (!data.results) return [];
  return data.results.map((r: any) => ({
    name: r.name,
    admin1: r.admin1,
    country: r.country,
    latitude: r.latitude,
    longitude: r.longitude,
    timezone: r.timezone,
  }));
}
