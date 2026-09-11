import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { en, type Translation } from "@/i18n/en";
import { ru } from "@/i18n/ru";
import { ar } from "@/i18n/ar";
import {
  STORAGE_KEY,
  directionOf,
  initialLanguage,
  type Language,
} from "@/i18n/language";

export { LANGUAGES, detectLanguage, directionOf, resolveLanguage } from "@/i18n/language";
export type { Language } from "@/i18n/language";

/**
 * Lightweight i18n for a three-language, single-bundle app.
 *
 * No i18n library: the resources are plain typed objects keyed off the English
 * one, so a missing or misspelled key is a build error rather than a key name
 * leaking into the page at runtime. That buys the main thing a library would
 * give us (safety) without the dependency, and the whole runtime is this file
 * plus the selection policy in language.ts.
 *
 * Translations are imported directly rather than lazily: all three together
 * are a few kilobytes, far less than the request that would fetch one.
 */
const RESOURCES: Record<Language, Translation> = { en, ru, ar };

interface I18nValue {
  lang: Language;
  dir: "ltr" | "rtl";
  t: Translation;
  setLang: (next: Language) => void;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Language>(initialLanguage);
  const dir = directionOf(lang);

  // The document element carries both, so the whole page (including anything
  // outside React, and the browser's own UI hints) follows the choice. This is
  // what makes Arabic actually right-to-left rather than merely translated:
  // the browser mirrors flex, grid and text alignment from dir alone.
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = dir;
  }, [lang, dir]);

  const setLang = useCallback((next: Language) => {
    setLangState(next);
    try {
      // Only a deliberate choice is persisted. Detection is never written
      // back, so a visitor who has not chosen keeps following their browser.
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* the choice still applies for this page load */
    }
  }, []);

  const value = useMemo<I18nValue>(
    () => ({ lang, dir, t: RESOURCES[lang], setLang }),
    [lang, dir, setLang],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}
