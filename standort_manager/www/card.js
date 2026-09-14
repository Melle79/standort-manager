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
    this.innerHTML = '';
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
    this.appendChild(karte);
  }
}

customElements.define('standort-manager-card', StandortManagerCard);

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
