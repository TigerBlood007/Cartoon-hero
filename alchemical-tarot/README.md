# Alchemical Tarot

A personal tarot & oracle companion, built as an installable web app (PWA) —
works on Android, iPhone, and desktop from one codebase, no app store needed.

## Features

- **78-card deck** with original long-form meanings — Jungian archetype
  theory and alchemical symbolism (nigredo/albedo/citrinitas/rubedo) woven
  through the 22 Major Arcana especially, drawing on the tradition of
  Marie-Louise von Franz and Jung's work on individuation.
- **Past · Present · Future spread**, single-card draws, and the ability to
  pull supporting cards mid-reading when a spread isn't clear yet.
- **Reversals**, toggleable.
- **Moon phase**, computed locally.
- **Birth chart** (Sun, Moon, Ascendant, and all planets) computed locally
  with a real astronomical model (`astronomy-engine`) — free, offline,
  no external chart service. An optional birthplace name lookup (via a
  free geocoding API) helps find exact coordinates; manual lat/long entry
  works fully offline.
- **Journal** — every saved reading (cards, moon phase, notes, optional AI
  synthesis) is stored locally on the device and can be revisited anytime.
- **Optional AI-synthesized readings** — bring your own Anthropic API key
  (Settings) to have a reading synthesized across the drawn cards, grounded
  in the traditional meanings. Without a key, full traditional meanings are
  always shown — nothing is gated behind AI.
- **Daily nudge notification**, best-effort (see in-app note on background
  delivery limits for a keyless, serverless PWA).

All data (journal, birth profile, API key, reminder setting) lives only in
the browser's local storage on the device it's used on. Nothing is sent to
any server of ours.

## Development

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
npm run preview   # serve the production build locally
```

## Installing on a phone/laptop

1. Deploy the contents of `dist/` to any static host (or run `npm run preview`
   on a machine reachable from the phone).
2. **Android (Chrome)**: open the site, tap the menu, choose "Add to Home
   screen" / "Install app".
3. **Windows (Chrome/Edge)**: open the site, click the install icon in the
   address bar, or menu → "Install Alchemical Tarot".

Once installed, the service worker caches the app shell so card meanings,
spreads, and the offline birth-chart calculator work without a connection —
only the birthplace name lookup needs network access.
