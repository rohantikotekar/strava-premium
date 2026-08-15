/**
 * Jargon definitions.
 *
 * Every term the UI shows gets a one-sentence definition and a one-sentence "why
 * you'd care" (FRONTEND_DESIGN.md § copy guidelines). Defined once, referenced
 * everywhere — a term explained differently in two places is a bug.
 */

export interface GlossaryEntry {
  term: string;
  what: string;
  why: string;
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  ctl: {
    term: "Fitness (CTL)",
    what: "Your average training load over the last 42 days.",
    why: "It rises slowly with consistent work and is the best single proxy for how much your body can currently handle.",
  },
  atl: {
    term: "Fatigue (ATL)",
    what: "Your average training load over the last 7 days.",
    why: "It spikes fast after hard weeks — high fatigue with high fitness means you're working, not broken.",
  },
  tsb: {
    term: "Form (TSB)",
    what: "Fitness minus fatigue.",
    why: "Positive means you're fresh and ready to race; deeply negative means you're carrying a lot of recent work.",
  },
  tss: {
    term: "Training Stress Score",
    what: "How hard a session was, where one hour at your threshold power equals 100.",
    why: "It lets a short hard ride and a long easy one be compared on one scale.",
  },
  trimp: {
    term: "TRIMP",
    what: "A heart-rate-based training load, weighted so time near your maximum counts for much more.",
    why: "It's how we measure load when you don't have a power meter.",
  },
  np: {
    term: "Normalized Power",
    what: "What a ride's power would have felt like had it been perfectly steady.",
    why: "Surging costs more than the plain average suggests, and this captures that.",
  },
  if: {
    term: "Intensity Factor",
    what: "Normalized power divided by your threshold power.",
    why: "0.75 is a steady endurance ride; 1.0 is an hour at your limit.",
  },
  ef: {
    term: "Efficiency Factor",
    what: "Power (or speed) divided by heart rate.",
    why: "If it trends up over months at the same heart rate, your aerobic engine is getting better.",
  },
  decoupling: {
    term: "Aerobic decoupling",
    what: "How much your heart rate drifted up in the second half of a session relative to the first, at the same output.",
    why: "Under 5% on a long steady effort is a sign of solid aerobic base.",
  },
  acwr: {
    term: "Acute:Chronic ratio",
    what: "Your last 7 days of load divided by your last 42.",
    why: "Ramping far above 1.3 for long is where a lot of people get hurt. This is information, not medical advice.",
  },
  ftp: {
    term: "FTP",
    what: "Functional Threshold Power — roughly the power you could hold for an hour.",
    why: "It anchors your power zones and every TSS number.",
  },
  gap: {
    term: "Grade Adjusted Pace",
    what: "Your pace corrected for the hill you were on.",
    why: "It makes a hilly run comparable to a flat one.",
  },
  power_curve: {
    term: "Power curve",
    what: "Your best sustained power for every duration, from one second to several hours.",
    why: "It shows exactly which part of your ability improved, rather than one summary number.",
  },
  load_source: {
    term: "Load source",
    what: "Which data we used to score a session: power, heart rate, your effort rating, or just its duration.",
    why: "A duration-based estimate isn't comparable to a power-based one, so we always tell you which it was.",
  },
};

export function glossary(key: string): GlossaryEntry | undefined {
  return GLOSSARY[key];
}
