/* Standort-Manager – Dashboard-Karte.
 *
 * Liest ausschließlich Entitäten aus Home Assistant, spricht also nicht mit
 * dem Add-on. Das macht sie unabhängig von Netzwerkpfaden und funktioniert
 * auch, wenn das Add-on gerade neu startet.
 *
 * Zur Sprache: Zustand und Fahrtrichtung kommen bereits übersetzt aus dem
 * Add-on (`state_text`), die Beschriftungen der Karte stehen unten im
 * eigenen kleinen Katalog. Die Karte folgt der Sprache von Home Assistant,
 * nicht der des Add-ons – im Dashboard erwartet man die HA-Sprache.
 *
 * Einbinden:
 *   type: custom:standort-manager-card
 *   title: Familie
 *   entities:            # optional; ohne Angabe alle standort_-Tracker
 *     - device_tracker.standort_sven_s_iphone
 *   columns: 2           # optional
 *   show_address: true   # optional
 */

const TEXTE = {
  de: {
    distance: 'Entfernung',
    travel: 'Fahrtdauer',
    arrival: 'Ankunft',
    battery: 'Akku',
    last_seen: 'Zuletzt',
    no_devices: 'Keine Standort-Manager-Geräte gefunden.',
    unavailable: 'nicht erreichbar',
    minutes: 'Min.',
    hours: 'Std.',
    ed_title: 'Überschrift',
    ed_title_hint: 'Leer lassen, um die Karte ohne Überschrift zu zeigen.',
    ed_columns: 'Spalten',
    ed_address: 'Adresse anzeigen',
    ed_entities: 'Geräte',
    ed_entities_hint: 'Nichts ausgewählt heißt: alle verfolgten Geräte, auch später hinzukommende.',
    ed_none: 'Keine Standort-Manager-Geräte gefunden. Ist das Add-on eingerichtet?',
  },
  en: {
    distance: 'Distance',
    travel: 'Travel',
    arrival: 'Arrival',
    battery: 'Battery',
    last_seen: 'Last seen',
    no_devices: 'No Location Manager devices found.',
    unavailable: 'unavailable',
    minutes: 'min',
    hours: 'h',
    ed_title: 'Heading',
    ed_title_hint: 'Leave empty to show the card without a heading.',
    ed_columns: 'Columns',
    ed_address: 'Show address',
    ed_entities: 'Devices',
    ed_entities_hint: 'Nothing selected means all tracked devices, including later additions.',
    ed_none: 'No Location Manager devices found. Is the add-on set up?',
  },
};

class StandortManagerCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      title: null,
      entities: null,
      columns: 2,
      show_address: true,
      ...config,
    };
    this._gebaut = false;
  }

  static getConfigElement() {
    return document.createElement('standort-manager-card-editor');
  }

  static getStubConfig() {
    return { title: 'Standort', columns: 2, show_address: true };
  }

  set hass(hass) {
    this._hass = hass;
    this._zeichne();
  }

  getCardSize() {
    return 1 + Math.ceil(this._geraete().length / (this._config.columns || 2)) * 2;
  }

  /* Welche Tracker gehören dazu? Ohne Angabe alle, die das Add-on
     angelegt hat – so wächst die Karte mit, wenn ein Gerät dazukommt. */
  _geraete() {
    if (!this._hass) return [];
    const ids = this._config.entities
      || Object.keys(this._hass.states)
        .filter((id) => id.startsWith('device_tracker.standort_'))
        .sort();
    return ids
      .map((id) => this._hass.states[id])
      .filter(Boolean);
  }

  _t(schluessel) {
    const sprache = (this._hass?.language || 'de').split('-')[0];
    return (TEXTE[sprache] || TEXTE.en)[schluessel] ?? schluessel;
  }

  /* Zu einem Tracker den passenden Sensor finden: gleiche Kurzform,
     anderes Suffix. */
  _sensor(tracker, endung) {
    const kurz = tracker.entity_id.replace('device_tracker.', '');
    return this._hass.states[`sensor.${kurz}_${endung}`];
  }

  _wert(tracker, endung) {
    const s = this._sensor(tracker, endung);
    if (!s || ['unknown', 'unavailable', ''].includes(s.state)) return null;
    return s;
  }

  _zeichne() {
    if (!this._gebaut) {
      this._bauen();
      this._gebaut = true;
    }
    const geraete = this._geraete();
    const ziel = this._inhalt;

    if (!geraete.length) {
      ziel.innerHTML = `<div class="leer">${this._t('no_devices')}</div>`;
      return;
    }

    ziel.style.gridTemplateColumns =
      `repeat(${Math.max(1, this._config.columns || 2)}, minmax(0, 1fr))`;
    ziel.innerHTML = geraete.map((g) => this._kachel(g)).join('');
  }

  _kachel(tracker) {
    const attrs = tracker.attributes;
    const zustand = attrs.state_key || 'unknown';
    const name = (attrs.friendly_name || tracker.entity_id)
      .replace(/\s+Standort$/i, '')
      .replace(/\s+Location$/i, '');

    const akku = this._wert(tracker, 'battery');
    const entfernung = this._wert(tracker, 'distance');
    const fahrt = this._wert(tracker, 'travel_time');
    const ankunft = this._wert(tracker, 'arrival_time');
    const adresse = this._wert(tracker, 'address');
    const gesehen = this._wert(tracker, 'last_seen');

    const zeilen = [];
    if (entfernung && zustand !== 'home') {
      zeilen.push([this._t('distance'),
        `${entfernung.state} ${entfernung.attributes.unit_of_measurement || ''}`]);
    }
    if (fahrt) {
      zeilen.push([this._t('travel'), `${fahrt.state} ${this._t('minutes')}`]);
    }
    if (ankunft) {
      zeilen.push([this._t('arrival'), this._uhrzeit(ankunft.state)]);
    }
    if (gesehen) {
      zeilen.push([this._t('last_seen'), this._relativ(gesehen.state)]);
    }

    const akkuKlasse = akku && Number(akku.state) <= 20 ? ' schwach' : '';

    return `
      <div class="kachel zustand-${zustand}" data-entity="${tracker.entity_id}">
        <div class="kopf">
          <span class="name">${this._sicher(name)}</span>
          ${akku ? `<span class="akku${akkuKlasse}">${akku.state} %</span>` : ''}
        </div>
        <div class="zustand">${this._sicher(attrs.state_text || tracker.state)}</div>
        ${this._config.show_address && adresse
          ? `<div class="adresse">${this._sicher(adresse.state)}</div>` : ''}
        ${zeilen.length ? `<div class="werte">${zeilen.map(([k, v]) =>
          `<div class="zeile"><span>${this._sicher(k)}</span><span>${this._sicher(v)}</span></div>`
        ).join('')}</div>` : ''}
      </div>`;
  }

  _uhrzeit(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString(this._hass.language || 'de',
      { hour: '2-digit', minute: '2-digit' });
  }

  _relativ(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const diff = Math.round((d.getTime() - Date.now()) / 1000);
    const rtf = new Intl.RelativeTimeFormat(this._hass.language || 'de',
      { numeric: 'auto' });
    const abs = Math.abs(diff);
    if (abs < 60) return rtf.format(diff, 'second');
    if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
    return rtf.format(Math.round(diff / 86400), 'day');
  }

  /* Namen und Adressen stammen aus fremden Quellen - nie ungeprüft ins
     Markup. */
  _sicher(text) {
    return String(text ?? '').replace(/[&<>"']/g, (z) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[z]));
  }

  _bauen() {
    // Shadow DOM, damit die Klassennamen dieser Karte nicht in andere
    // Karten des Dashboards strahlen. Im Light DOM sind Klassen wie
    // .zeile oder .kachel global - und .zeile gibt es anderswo auch.
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = '';
    const karte = document.createElement('ha-card');
    if (this._config.title) karte.setAttribute('header', this._config.title);

    const stil = document.createElement('style');
    stil.textContent = `
      .inhalt {
        display: grid;
        gap: 8px;
        padding: 12px;
      }
      .kachel {
        border: 1px solid var(--divider-color, #e0e0e0);
        border-left: 4px solid var(--secondary-text-color, #888);
        border-radius: 10px;
        padding: 10px 12px;
        cursor: pointer;
        background: var(--ha-card-background, transparent);
      }
      .kachel:hover { border-color: var(--primary-color); }
      .kachel.zustand-home { border-left-color: var(--label-badge-green, #43a047); }
      .kachel.zustand-in_zone { border-left-color: var(--primary-color, #03a9f4); }
      .kachel.zustand-traveling { border-left-color: var(--label-badge-yellow, #f4b400); }
      .kachel.zustand-offline { border-left-color: var(--label-badge-red, #db4437); }
      .kopf { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
      .name { font-weight: 500; }
      .akku { font-size: 12px; color: var(--secondary-text-color); }
      .akku.schwach { color: var(--label-badge-red, #db4437); }
      .zustand { font-size: 14px; margin-top: 2px; }
      .adresse { font-size: 12px; color: var(--secondary-text-color); margin-top: 2px; }
      .werte { margin-top: 8px; }
      .zeile {
        display: flex;
        justify-content: space-between;
        font-size: 12px;
        padding: 1px 0;
      }
      .zeile span:first-child { color: var(--secondary-text-color); }
      .leer { padding: 24px; text-align: center; color: var(--secondary-text-color); }
      @media (max-width: 500px) {
        .inhalt { grid-template-columns: 1fr !important; }
      }
    `;

    this._inhalt = document.createElement('div');
    this._inhalt.className = 'inhalt';
    // Ein Klick öffnet den gewohnten Detaildialog von Home Assistant.
    this._inhalt.addEventListener('click', (ev) => {
      const kachel = ev.target.closest('.kachel');
      if (!kachel) return;
      const ereignis = new Event('hass-more-info', { bubbles: true, composed: true });
      ereignis.detail = { entityId: kachel.dataset.entity };
      this.dispatchEvent(ereignis);
    });

    karte.appendChild(stil);
    karte.appendChild(this._inhalt);
    this.shadowRoot.appendChild(karte);
  }
}

customElements.define('standort-manager-card', StandortManagerCard);

/* --- Visueller Editor ---------------------------------------------------
 *
 * Ohne dieses Element endet "Karte bearbeiten" im Dashboard mit einem
 * Fehler, weil getConfigElement() darauf verweist. Bewusst mit nativen
 * Eingabefeldern statt ha-form: die Schema-API von Home Assistant ändert
 * sich zwischen Versionen, einfache Felder tun das nicht.
 */
class StandortManagerCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._zeichne();
  }

  set hass(hass) {
    this._hass = hass;
    this._zeichne();
  }

  _t(schluessel) {
    const sprache = (this._hass?.language || 'de').split('-')[0];
    return (TEXTE[sprache] || TEXTE.en)[schluessel] ?? schluessel;
  }

  _melde(aenderung) {
    this._config = { ...this._config, ...aenderung };
    // Leere Werte wieder entfernen, damit die YAML-Ansicht aufgeräumt bleibt.
    for (const [k, v] of Object.entries(this._config)) {
      if (v === '' || v === null || (Array.isArray(v) && !v.length)) {
        delete this._config[k];
      }
    }
    const ereignis = new CustomEvent('config-changed', {
      detail: { config: this._config },
      bubbles: true,
      composed: true,
    });
    this.dispatchEvent(ereignis);
  }

  _verfuegbar() {
    if (!this._hass) return [];
    return Object.keys(this._hass.states)
      .filter((id) => id.startsWith('device_tracker.standort_'))
      .sort()
      .map((id) => ({
        id,
        name: (this._hass.states[id].attributes.friendly_name || id)
          .replace(/\s+Standort$/i, '').replace(/\s+Location$/i, ''),
      }));
  }

  _zeichne() {
    if (!this._config || !this._hass) return;
    if (!this._aufgebaut) {
      this._aufbauen();
      this._aufgebaut = true;
    }

    this._titel.value = this._config.title ?? '';
    this._spalten.value = String(this._config.columns ?? 2);
    this._adresse.checked = this._config.show_address !== false;

    const geraete = this._verfuegbar();
    this._liste.innerHTML = '';
    if (!geraete.length) {
      this._liste.appendChild(this._absatz(this._t('ed_none')));
      return;
    }
    const gewaehlt = this._config.entities || [];
    for (const g of geraete) {
      const zeile = document.createElement('label');
      zeile.className = 'zeile';
      const haken = document.createElement('input');
      haken.type = 'checkbox';
      haken.checked = gewaehlt.includes(g.id);
      haken.onchange = () => {
        const aktuell = new Set(this._config.entities || []);
        if (haken.checked) aktuell.add(g.id);
        else aktuell.delete(g.id);
        this._melde({ entities: [...aktuell] });
      };
      const text = document.createElement('span');
      text.textContent = g.name;
      zeile.append(haken, text);
      this._liste.appendChild(zeile);
    }
  }

  _absatz(text) {
    const p = document.createElement('p');
    p.className = 'hinweis';
    p.textContent = text;
    return p;
  }

  _feld(beschriftung, element, hinweis) {
    const huelle = document.createElement('div');
    huelle.className = 'feld';
    const label = document.createElement('label');
    label.textContent = beschriftung;
    huelle.append(label, element);
    if (hinweis) huelle.appendChild(this._absatz(hinweis));
    return huelle;
  }

  _aufbauen() {
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = '';
    const stil = document.createElement('style');
    stil.textContent = `
      .editor { padding: 8px 0; }
      .feld { margin-bottom: 14px; }
      .feld > label { display: block; font-size: 13px; margin-bottom: 4px;
                      color: var(--secondary-text-color); }
      .editor input[type="text"], .editor select {
        width: 100%; padding: 9px 10px; font-size: 14px;
        border: 1px solid var(--divider-color, #ccc); border-radius: 8px;
        background: var(--secondary-background-color, #fafafa);
        color: var(--primary-text-color); box-sizing: border-box;
      }
      .zeile { display: flex; align-items: center; gap: 8px; padding: 3px 0;
               font-size: 14px; cursor: pointer; }
      .zeile input { margin: 0; }
      .hinweis { font-size: 12px; color: var(--secondary-text-color);
                 margin: 4px 0 0; }
      .liste { border: 1px solid var(--divider-color, #ccc); border-radius: 8px;
               padding: 8px 10px; max-height: 220px; overflow-y: auto; }
    `;

    const wurzel = document.createElement('div');
    wurzel.className = 'editor';

    this._titel = document.createElement('input');
    this._titel.type = 'text';
    this._titel.oninput = () => this._melde({ title: this._titel.value });
    wurzel.appendChild(this._feld(this._t('ed_title'), this._titel,
                                  this._t('ed_title_hint')));

    this._spalten = document.createElement('select');
    for (const n of [1, 2, 3, 4]) {
      const option = document.createElement('option');
      option.value = String(n);
      option.textContent = String(n);
      this._spalten.appendChild(option);
    }
    this._spalten.onchange = () =>
      this._melde({ columns: Number(this._spalten.value) });
    wurzel.appendChild(this._feld(this._t('ed_columns'), this._spalten));

    const adresszeile = document.createElement('label');
    adresszeile.className = 'zeile';
    this._adresse = document.createElement('input');
    this._adresse.type = 'checkbox';
    this._adresse.onchange = () =>
      this._melde({ show_address: this._adresse.checked });
    const adresstext = document.createElement('span');
    adresstext.textContent = this._t('ed_address');
    adresszeile.append(this._adresse, adresstext);
    const adressfeld = document.createElement('div');
    adressfeld.className = 'feld';
    adressfeld.appendChild(adresszeile);
    wurzel.appendChild(adressfeld);

    this._liste = document.createElement('div');
    this._liste.className = 'liste';
    wurzel.appendChild(this._feld(this._t('ed_entities'), this._liste,
                                  this._t('ed_entities_hint')));

    this.shadowRoot.append(stil, wurzel);
  }
}

customElements.define('standort-manager-card-editor', StandortManagerCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'standort-manager-card',
  name: 'Standort-Manager',
  description: 'Zeigt die vom Standort-Manager verfolgten Geräte.',
  preview: true,
});

console.info('%c STANDORT-MANAGER-CARD %c geladen ',
  'color:white;background:#1c7ed6;font-weight:700',
  'color:#1c7ed6;background:white');
