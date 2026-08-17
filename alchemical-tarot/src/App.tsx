import { useEffect, useMemo, useState } from 'react';
import { Welcome } from './components/Welcome';
import { Nav, type View } from './components/Nav';
import { DrawScreen } from './components/DrawScreen';
import { Journal } from './components/Journal';
import { AstrologyScreen } from './components/AstrologyScreen';
import { Settings } from './components/Settings';
import { getMoonPhase } from './lib/astro';
import { loadReadings, saveReading, deleteReading } from './lib/journal';
import { getApiKey, setApiKey, getBirthProfile, setBirthProfile, getNotifyHour, setNotifyHour, type BirthProfile } from './lib/settings';
import { startNudgeWatcher } from './lib/notifications';
import type { Reading } from './data/cardTypes';

const VISITED_KEY = 'alchemical-tarot-visited';

export default function App() {
  const [visited, setVisited] = useState(() => localStorage.getItem(VISITED_KEY) === '1');
  const [view, setView] = useState<View>('draw');
  const [readings, setReadings] = useState<Reading[]>(() => loadReadings());
  const [apiKey, setApiKeyState] = useState(() => getApiKey());
  const [profile, setProfile] = useState<BirthProfile | null>(() => getBirthProfile());
  const [notifyHour, setNotifyHourState] = useState<number | null>(() => getNotifyHour());

  const moon = useMemo(() => getMoonPhase(), []);

  useEffect(() => {
    const stop = startNudgeWatcher(() => notifyHour);
    return stop;
  }, [notifyHour]);

  function enter() {
    localStorage.setItem(VISITED_KEY, '1');
    setVisited(true);
  }

  function handleSaveReading(reading: Reading) {
    setReadings(saveReading(reading));
  }

  function handleDeleteReading(id: string) {
    setReadings(deleteReading(id));
  }

  function handleApiKeyChange(key: string) {
    setApiKey(key);
    setApiKeyState(key);
  }

  function handleSaveProfile(p: BirthProfile) {
    setBirthProfile(p);
    setProfile(p);
  }

  function handleNotifyHourChange(hour: number | null) {
    setNotifyHour(hour);
    setNotifyHourState(hour);
  }

  function handleResetData() {
    if (!confirm('This clears the journal, birth chart, API key, and reminder settings on this device. Continue?')) return;
    localStorage.clear();
    setReadings([]);
    setApiKeyState('');
    setProfile(null);
    setNotifyHourState(null);
  }

  if (!visited) {
    return <Welcome onEnter={enter} />;
  }

  return (
    <>
      <div className="screen">
        {view === 'draw' && <DrawScreen moon={moon} apiKey={apiKey} onSave={handleSaveReading} />}
        {view === 'journal' && <Journal readings={readings} onDelete={handleDeleteReading} />}
        {view === 'astrology' && <AstrologyScreen moon={moon} profile={profile} onSaveProfile={handleSaveProfile} />}
        {view === 'settings' && (
          <Settings
            apiKey={apiKey}
            onApiKeyChange={handleApiKeyChange}
            notifyHour={notifyHour}
            onNotifyHourChange={handleNotifyHourChange}
            onResetData={handleResetData}
          />
        )}
      </div>
      <Nav current={view} onChange={setView} />
    </>
  );
}
