/**
 * Language selection: which languages exist, how one is detected, and where a
 * manual choice is remembered.
 *
 * Deliberately free of React and of the translation resources, so the policy
 * can be exercised directly - the mapping from a browser's Accept-Language
 * list to a supported language is the kind of thing that is easy to get subtly
 * wrong and worth testing on its own.
 */

export type Language = "en" | "ru" | "ar";

export const LANGUAGES: { code: Language; label: string; dir: "ltr" | "rtl" }[] = [
  { code: "en", label: "EN", dir: "ltr" },
  { code: "ru", label: "RU", dir: "ltr" },
  { code: "ar", label: "AR", dir: "rtl" },
];

/** Stable across releases: changing it would silently reset everyone's choice. */
export const STORAGE_KEY = "cis.language";

export function directionOf(language: Language): "ltr" | "rtl" {
  return language === "ar" ? "rtl" : "ltr";
}

export function isLanguage(value: unknown): value is Language {
  return value === "en" || value === "ru" || value === "ar";
}

/**
 * The browser's preference, mapped onto what we actually support.
 *
 * Matches on the primary subtag only, so ru-RU, ru-KZ and ar-EG all resolve
 * the way a reader would expect. The list is in priority order, so the first
 * language we speak wins. Anything we do not speak falls back to English.
 */
export function detectLanguage(candidates: readonly string[]): Language {
  for (const tag of candidates) {
    const primary = tag.toLowerCase().split("-")[0];
    if (primary === "ru" || primary === "ar" || primary === "en") return primary;
  }
  return "en";
}

/** A manual choice from a previous visit, if the browser still has one. */
export function storedLanguage(): Language | null {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return isLanguage(saved) ? saved : null;
  } catch {
    // Private modes and blocked site data both throw here.
    return null;
  }
}

/**
 * Resolve the language for this page load.
 *
 * A stored manual choice always wins; otherwise the browser decides. Detection
 * is never written back to storage, so a visitor who has not chosen keeps
 * following their browser if they change it later.
 */
export function resolveLanguage(
  stored: Language | null,
  candidates: readonly string[],
): Language {
  return stored ?? detectLanguage(candidates);
}

export function browserLanguages(): readonly string[] {
  if (typeof navigator === "undefined") return [];
  return navigator.languages ?? (navigator.language ? [navigator.language] : []);
}

export function initialLanguage(): Language {
  return resolveLanguage(storedLanguage(), browserLanguages());
}
