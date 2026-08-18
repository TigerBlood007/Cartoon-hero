import Anthropic from '@anthropic-ai/sdk';
import type { DrawnCard } from '../data/cardTypes';
import type { MoonPhaseInfo } from './astro';
import type { AiModel } from './settings';

interface SynthesisInput {
  cards: { label: string; drawn: DrawnCard }[];
  question?: string;
  moon: MoonPhaseInfo;
  model: AiModel;
}

// Calls Anthropic directly from the browser using a key the user supplies
// and stores locally (Settings screen). This never ships a bundled key —
// without one, callers should fall back to the traditional written
// meanings instead of calling this at all.
export async function synthesizeReading(apiKey: string, input: SynthesisInput): Promise<string> {
  const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true });

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

  try {
    const response = await client.messages.create({
      model: input.model,
      max_tokens: 1500,
      messages: [{ role: 'user', content: prompt }],
    });

    if (response.stop_reason === 'refusal') {
      throw new Error('The model declined to generate this reading.');
    }

    const textBlock = response.content.find((block) => block.type === 'text');
    return textBlock?.text ?? '';
  } catch (error) {
    if (error instanceof Anthropic.AuthenticationError) {
      throw new Error('That API key was rejected — double-check it in Settings.');
    }
    if (error instanceof Anthropic.RateLimitError) {
      throw new Error('Rate limited by Anthropic — try again in a moment.');
    }
    if (error instanceof Anthropic.APIError) {
      throw new Error(`AI synthesis failed (${error.status}): ${error.message}`);
    }
    throw error;
  }
}
