import type { DrawnCard } from '../data/cardTypes';
import type { MoonPhaseInfo } from './astro';

interface SynthesisInput {
  cards: { label: string; drawn: DrawnCard }[];
  question?: string;
  moon: MoonPhaseInfo;
}

// Calls the Anthropic API directly from the browser using a key the user
// supplies and stores locally (Settings screen). This never ships a
// bundled key — without one, callers should fall back to the traditional
// written meanings instead of calling this at all.
export async function synthesizeReading(apiKey: string, input: SynthesisInput): Promise<string> {
  const cardLines = input.cards
    .map(({ label, drawn }) => {
      const orientation = drawn.reversed ? 'reversed' : 'upright';
      return `${label}: ${drawn.card.name} (${orientation}) — traditional meaning: ${
        drawn.reversed ? drawn.card.reversed : drawn.card.upright
      }`;
    })
    .join('\n');

  const prompt = `You are helping synthesize a tarot reading for an experienced reader who values traditional symbolism, Jungian archetype theory, and alchemical imagery (nigredo/albedo/citrinitas/rubedo) over generic AI positivity. She reads intuitively and already knows the individual card meanings below — what she wants from you is how they connect to each other and to her question, in a grounded, specific way. Do not repeat the individual card meanings back to her; weave them into one coherent reading. Keep it to 3-5 short paragraphs. Avoid vague affirmations ("trust the journey") in favor of concrete, symbolic observations.

Current moon phase: ${input.moon.name} (${Math.round(input.moon.illuminationFraction * 100)}% illuminated)
${input.question ? `Her question: ${input.question}` : 'No specific question — a general reading.'}

Cards drawn:
${cardLines}`;

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-5',
      max_tokens: 1024,
      messages: [{ role: 'user', content: prompt }],
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`AI synthesis failed (${response.status}): ${text.slice(0, 200)}`);
  }

  const data = await response.json();
  const textBlock = data.content?.find((block: any) => block.type === 'text');
  return textBlock?.text ?? '';
}
