import { LANGUAGES } from "@/i18n/language";
import { useI18n } from "@/i18n";

/**
 * EN | RU | AR.
 *
 * Deliberately the same chip styling the dashboard's status filter uses, so it
 * reads as part of the existing interface rather than an add-on. Each option
 * is labelled with its own language name for screen readers, which is what a
 * reader who cannot see the two-letter code needs.
 */
export function LanguageSwitcher() {
  const { lang, setLang, t } = useI18n();

  return (
    <div className="lang-switch" role="group" aria-label={t.nav.language}>
      {LANGUAGES.map((option) => (
        <button
          key={option.code}
          type="button"
          className={`lang-btn ${lang === option.code ? "active" : ""}`}
          aria-pressed={lang === option.code}
          // The name of the language, in that language - not the current one.
          lang={option.code}
          title={option.code === "en" ? "English" : option.code === "ru" ? "Русский" : "العربية"}
          onClick={() => setLang(option.code)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
