/* Übersetzung in der Oberfläche.
 *
 * Spiegelbild von app/i18n.py: derselbe Katalog, dieselben Schlüssel,
 * dieselbe Rückfallkette. Die Oberfläche holt den Katalog einmal beim
 * Start und noch einmal bei jedem Sprachwechsel.
 *
 * Übersetzt wird über Attribute im HTML, nicht über eingebaute Strings:
 *   <span data-i18n="nav.devices"></span>
 *   <input data-i18n-placeholder="common.search">
 *   <span data-i18n="unit.minutes_short" data-i18n-params='{"count":5}'>
 */

const I18n = {
  lang: 'de',
  strings: {},
  languages: [],

  async init() {
    await this.loadLanguages();
    await this.load(this.languages.current || 'de');
  },

  async loadLanguages() {
    try {
      const resp = await fetch(api('languages'));
      const data = await resp.json();
      this.languages = data.available || [];
      this.languages.current = data.current;
      return data;
    } catch (err) {
      console.error('Sprachliste nicht ladbar', err);
      return { available: [], current: 'de' };
    }
  },

  async load(lang) {
    try {
      const resp = await fetch(api(`i18n/${encodeURIComponent(lang)}`));
      const data = await resp.json();
      this.lang = data.lang;
      this.strings = data.strings || {};
      document.documentElement.lang = this.lang;
      this.apply();
      return true;
    } catch (err) {
      console.error('Katalog nicht ladbar', err);
      return false;
    }
  },

  /* Ein Schlüssel wird zu Text. Fehlt er, erscheint der Schlüssel selbst –
     sichtbar genug, um beim Übersetzen aufzufallen, aber nie leer. */
  t(key, params) {
    let text = this.strings[key];
    if (text === undefined) {
      console.debug('Übersetzung fehlt:', key);
      return key;
    }
    if (params) {
      text = text.replace(/\{(\w+)\}/g, (match, name) =>
        params[name] !== undefined ? params[name] : match
      );
    }
    return text;
  },

  /* Alle markierten Stellen im Dokument übersetzen. */
  apply(root) {
    const scope = root || document;

    scope.querySelectorAll('[data-i18n]').forEach((el) => {
      const params = el.dataset.i18nParams
        ? JSON.parse(el.dataset.i18nParams)
        : null;
      el.textContent = this.t(el.dataset.i18n, params);
    });

    scope.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
      el.placeholder = this.t(el.dataset.i18nPlaceholder);
    });

    scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
      el.title = this.t(el.dataset.i18nTitle);
    });

    scope.querySelectorAll('[data-i18n-aria]').forEach((el) => {
      el.setAttribute('aria-label', this.t(el.dataset.i18nAria));
    });
  },
};

/* --- Formatierung, die von der Sprache abhängt ------------------------- */

const Format = {
  units: 'metrisch',
  timeFormat: '24h',

  distance(meters) {
    if (meters === null || meters === undefined) return '–';
    if (this.units === 'imperial') {
      const mi = meters / 1609.344;
      return mi < 0.1
        ? `${Math.round(meters * 3.28084)} ${I18n.t('unit.ft')}`
        : `${mi.toFixed(mi < 10 ? 1 : 0)} ${I18n.t('unit.mi')}`;
    }
    return meters < 1000
      ? `${Math.round(meters)} ${I18n.t('unit.m')}`
      : `${(meters / 1000).toFixed(meters < 10000 ? 1 : 0)} ${I18n.t('unit.km')}`;
  },

  speed(ms) {
    if (ms === null || ms === undefined) return '–';
    return this.units === 'imperial'
      ? `${Math.round(ms * 2.23694)} ${I18n.t('unit.mph')}`
      : `${Math.round(ms * 3.6)} ${I18n.t('unit.kmh')}`;
  },

  duration(seconds) {
    if (seconds === null || seconds === undefined) return '–';
    const min = Math.round(seconds / 60);
    if (min < 60) return I18n.t('unit.minutes_short', { count: min });
    const h = Math.floor(min / 60);
    const rest = min % 60;
    return rest
      ? `${I18n.t('unit.hours_short', { count: h })} ${I18n.t('unit.minutes_short', { count: rest })}`
      : I18n.t('unit.hours_short', { count: h });
  },

  time(timestamp) {
    if (!timestamp) return I18n.t('common.never');
    const d = new Date(timestamp * 1000);
    return d.toLocaleTimeString(I18n.lang, {
      hour: '2-digit',
      minute: '2-digit',
      hour12: this.timeFormat === '12h',
    });
  },

  dateTime(timestamp) {
    if (!timestamp) return I18n.t('common.never');
    const d = new Date(timestamp * 1000);
    return d.toLocaleString(I18n.lang, {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: this.timeFormat === '12h',
    });
  },

  /* Relative Zeit – nutzt die eingebaute Lokalisierung des Browsers,
     damit nicht jede Sprache eigene Regeln im Katalog braucht. */
  ago(timestamp) {
    if (!timestamp) return I18n.t('common.never');
    const diff = Math.round(timestamp - Date.now() / 1000);
    const abs = Math.abs(diff);
    const rtf = new Intl.RelativeTimeFormat(I18n.lang, { numeric: 'auto' });
    if (abs < 60) return rtf.format(Math.round(diff), 'second');
    if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
    return rtf.format(Math.round(diff / 86400), 'day');
  },
};

/* Ingress läuft unter einem langen Präfixpfad. Relative Pfade brechen,
   sobald die Adresse ohne abschließenden Schrägstrich aufgerufen wird –
   deshalb die Basis einmal sauber bestimmen. */
function api(pfad) {
  let basis = window.location.pathname;
  if (!basis.endsWith('/')) basis = basis.replace(/[^/]*$/, '');
  return `${basis}api/${pfad}`;
}
