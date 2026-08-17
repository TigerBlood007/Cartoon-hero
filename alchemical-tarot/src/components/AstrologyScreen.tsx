import { useState } from 'react';
import type { MoonPhaseInfo, NatalChart, GeocodeResult } from '../lib/astro';
import { buildNatalChart, lookupBirthplace } from '../lib/astro';
import type { BirthProfile } from '../lib/settings';

const PLANET_ROWS: { key: keyof NatalChart; label: string }[] = [
  { key: 'sun', label: 'Sun' },
  { key: 'moon', label: 'Moon' },
  { key: 'ascendant', label: 'Ascendant' },
  { key: 'mercury', label: 'Mercury' },
  { key: 'venus', label: 'Venus' },
  { key: 'mars', label: 'Mars' },
  { key: 'jupiter', label: 'Jupiter' },
  { key: 'saturn', label: 'Saturn' },
  { key: 'uranus', label: 'Uranus' },
  { key: 'neptune', label: 'Neptune' },
  { key: 'pluto', label: 'Pluto' },
];

export function AstrologyScreen({
  moon,
  profile,
  onSaveProfile,
}: {
  moon: MoonPhaseInfo;
  profile: BirthProfile | null;
  onSaveProfile: (p: BirthProfile) => void;
}) {
  const [date, setDate] = useState(profile?.date ?? '');
  const [time, setTime] = useState(profile?.time ?? '');
  const [utcOffset, setUtcOffset] = useState(profile?.utcOffsetHours ?? -5);
  const [latitude, setLatitude] = useState<number | null>(profile?.latitude ?? null);
  const [longitude, setLongitude] = useState<number | null>(profile?.longitude ?? null);
  const [placeName, setPlaceName] = useState(profile?.placeName ?? '');
  const [placeQuery, setPlaceQuery] = useState('');
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [chart, setChart] = useState<NatalChart | null>(null);

  async function search() {
    if (!placeQuery.trim()) return;
    setSearching(true);
    setSearchError('');
    try {
      const found = await lookupBirthplace(placeQuery.trim());
      setResults(found);
      if (found.length === 0) setSearchError('No matches — try a nearby larger city, or enter coordinates manually below.');
    } catch {
      setSearchError('Could not reach the lookup service (offline?). Enter latitude/longitude manually below.');
    } finally {
      setSearching(false);
    }
  }

  function pickResult(r: GeocodeResult) {
    setLatitude(r.latitude);
    setLongitude(r.longitude);
    setPlaceName(`${r.name}${r.admin1 ? ', ' + r.admin1 : ''}, ${r.country}`);
    setResults([]);
    setPlaceQuery('');
  }

  function computeChart() {
    if (!date) return;
    const built = buildNatalChart({
      date,
      time: time || undefined,
      utcOffsetHours: utcOffset,
      latitude: latitude ?? undefined,
      longitude: longitude ?? undefined,
    });
    setChart(built);
    onSaveProfile({ date, time, utcOffsetHours: utcOffset, latitude, longitude, placeName });
  }

  return (
    <div>
      <h1>Astrology</h1>

      <div className="panel moon-hero">
        <div className="emoji">{moon.glyph}</div>
        <h2>{moon.name}</h2>
        <p className="muted">{Math.round(moon.illuminationFraction * 100)}% illuminated, right now</p>
      </div>

      <h2>Birth Chart</h2>
      <p className="muted">
        Planetary positions are computed locally from a real astronomical model — no external
        chart service, no cost. Only the birthplace name lookup below touches the network; you
        can skip it and enter coordinates by hand to stay fully offline.
      </p>

      <div className="panel">
        <div className="field">
          <label>Birth date</label>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div className="field">
          <label>Birth time (optional, improves accuracy — required for Ascendant)</label>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
        <div className="field">
          <label>UTC offset at birth (e.g. -5 for US Eastern Standard)</label>
          <input type="number" step="0.5" value={utcOffset} onChange={(e) => setUtcOffset(Number(e.target.value))} />
        </div>

        <div className="field">
          <label>Look up birthplace</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={placeQuery}
              onChange={(e) => setPlaceQuery(e.target.value)}
              placeholder="City, State/Country"
              onKeyDown={(e) => e.key === 'Enter' && search()}
            />
            <button className="btn" onClick={search} disabled={searching}>
              {searching ? '…' : 'Search'}
            </button>
          </div>
          {searchError && <p className="muted">{searchError}</p>}
          {results.length > 0 && (
            <div className="panel" style={{ marginTop: 8, padding: 8 }}>
              {results.map((r, i) => (
                <button
                  key={i}
                  className="btn btn-ghost btn-block"
                  style={{ marginBottom: 4, justifyContent: 'flex-start' }}
                  onClick={() => pickResult(r)}
                >
                  {r.name}{r.admin1 ? `, ${r.admin1}` : ''}, {r.country}
                </button>
              ))}
            </div>
          )}
        </div>

        {placeName && <p className="muted">Selected: {placeName}</p>}

        <div style={{ display: 'flex', gap: 8 }}>
          <div className="field" style={{ flex: 1 }}>
            <label>Latitude</label>
            <input type="number" step="0.0001" value={latitude ?? ''} onChange={(e) => setLatitude(e.target.value ? Number(e.target.value) : null)} placeholder="manual entry" />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Longitude</label>
            <input type="number" step="0.0001" value={longitude ?? ''} onChange={(e) => setLongitude(e.target.value ? Number(e.target.value) : null)} placeholder="manual entry" />
          </div>
        </div>

        <button className="btn btn-primary btn-block" onClick={computeChart} disabled={!date}>
          Calculate Chart
        </button>
      </div>

      {chart && (
        <div className="panel">
          <h3>Placements</h3>
          <div className="chart-grid">
            {PLANET_ROWS.map(({ key, label }) => {
              const pos = chart[key];
              return (
                <div className="chart-item" key={key}>
                  <span className="g">{pos ? pos.glyph : '—'}</span>
                  <div>
                    <div className="t">{label}</div>
                    <div className="v">{pos ? `${pos.sign} ${pos.degreeInSign.toFixed(1)}°` : 'need birth time + place'}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
