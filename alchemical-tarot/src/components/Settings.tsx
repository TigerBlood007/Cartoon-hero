import { useState } from 'react';
import type { AiModel } from '../lib/settings';

const MODEL_OPTIONS: { id: AiModel; name: string; price: string; blurb: string }[] = [
  {
    id: 'claude-haiku-4-5',
    name: 'Haiku 4.5',
    price: '$1 / $5 per million tokens (in/out)',
    blurb: 'Cheapest. Fine for quick, plainer summaries — least likely to nail specific symbolism.',
  },
  {
    id: 'claude-sonnet-5',
    name: 'Sonnet 5 (recommended)',
    price: '$3 / $15 per million tokens (in/out)',
    blurb: 'Best value here — noticeably richer, more specific symbolic writing than Haiku, for a small fraction of Opus\'s cost. At a reading or two a day, expect well under $1/month.',
  },
  {
    id: 'claude-opus-5',
    name: 'Opus 5',
    price: '$5 / $25 per million tokens (in/out)',
    blurb: 'The most capable model available. Marginally deeper readings than Sonnet for this kind of writing, at roughly 2x the cost — still cheap at this volume, just not the best value.',
  },
];

export function Settings({
  apiKey,
  onApiKeyChange,
  aiModel,
  onAiModelChange,
  notifyHour,
  onNotifyHourChange,
  onResetData,
}: {
  apiKey: string;
  onApiKeyChange: (key: string) => void;
  aiModel: AiModel;
  onAiModelChange: (model: AiModel) => void;
  notifyHour: number | null;
  onNotifyHourChange: (hour: number | null) => void;
  onResetData: () => void;
}) {
  const [keyInput, setKeyInput] = useState(apiKey);
  const [permission, setPermission] = useState(
    typeof Notification !== 'undefined' ? Notification.permission : 'unsupported',
  );

  async function enableNotifications(hour: number) {
    if (typeof Notification === 'undefined') return;
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === 'granted') {
      onNotifyHourChange(hour);
    }
  }

  return (
    <div>
      <h1>Settings</h1>

      <div className="panel">
        <h2>AI Reading Synthesis</h2>
        <p>
          Optional. Paste your own Anthropic API key to let the app weave a synthesized reading
          across the cards you've drawn, grounded in the traditional meanings and Jungian/alchemical
          framing built into this app. Without a key, you'll still see full traditional card meanings —
          nothing is locked behind AI.
        </p>
        <p className="muted">
          Your key is stored only in this browser's local storage on this device, and is sent
          directly from your device to Anthropic's API when you request a synthesis — it never
          passes through any server of ours. Clear it any time below.
        </p>
        <div className="field">
          <label>Anthropic API key</label>
          <input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="sk-ant-..."
          />
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={() => onApiKeyChange(keyInput)}>Save Key</button>
          <button className="btn btn-ghost" onClick={() => { setKeyInput(''); onApiKeyChange(''); }}>Clear</button>
        </div>

        <hr className="divider" />

        <h3>Model</h3>
        <p className="muted">
          Prices are current Anthropic API rates (Aug 2026). Sonnet 5 is the best value for this —
          the writing needs to be specific and grounded, not just fast or cheap, and at a
          reading-or-two-a-day pace the cost difference between all three options is fractions of
          a cent per reading either way.
        </p>
        {MODEL_OPTIONS.map((opt) => (
          <label
            key={opt.id}
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'flex-start',
              padding: '10px 0',
              borderTop: '1px solid var(--border-faint)',
              cursor: 'pointer',
            }}
          >
            <input
              type="radio"
              name="ai-model"
              checked={aiModel === opt.id}
              onChange={() => onAiModelChange(opt.id)}
              style={{ marginTop: 3 }}
            />
            <span>
              <strong style={{ color: 'var(--ink)' }}>{opt.name}</strong>
              <br />
              <span className="muted">{opt.price}</span>
              <br />
              <span className="muted">{opt.blurb}</span>
            </span>
          </label>
        ))}
      </div>

      <div className="panel">
        <h2>Daily Nudge</h2>
        <p>A gentle reminder to pull a card, at whatever hour suits her rhythm.</p>
        <p className="muted">
          Note: browser/PWA notifications on Android and Windows fire reliably while the app has
          been opened recently and notification permission is granted. Fully closing the app for
          long stretches can delay delivery — this isn't a dedicated push-notification server, just
          a local reminder the app schedules for itself. Opening the app now and then keeps it accurate.
        </p>
        <div className="field">
          <label>Reminder hour (24h, local time)</label>
          <input
            type="number"
            min={0}
            max={23}
            value={notifyHour ?? ''}
            onChange={(e) => e.target.value && enableNotifications(Number(e.target.value))}
            placeholder="e.g. 8 for 8am"
          />
        </div>
        {permission === 'denied' && <p style={{ color: 'var(--danger)' }}>Notifications are blocked in browser settings.</p>}
        {notifyHour !== null && (
          <button className="btn btn-ghost" onClick={() => onNotifyHourChange(null)}>Turn off reminder</button>
        )}
      </div>

      <div className="panel">
        <h2>Data</h2>
        <p>Everything — journal entries, birth chart, API key — lives only in this browser, on this device.</p>
        <button className="btn btn-danger btn-block" onClick={onResetData}>Clear All Local Data</button>
      </div>
    </div>
  );
}
