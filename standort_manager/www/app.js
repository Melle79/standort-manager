/* Standort-Manager – Oberfläche.
 *
 * Bewusst ohne Framework: das Add-on soll ohne Bauschritt im Docker-Image
 * landen. Alles, was der Benutzer liest, kommt aus dem Katalog – im Code
 * steht kein einziger fertiger Satz.
 */

const Zustand = {
  status: null,
  geraete: [],
  zonen: [],
  tracker: [],
  einstellungen: null,
  ansicht: 'uebersicht',
  kontoHinzufuegen: false,
  // Welche Gerätekarten offen sind - damit ein Neuzeichnen sie nicht zuklappt.
  offeneGeraete: new Set(),
  timer: null,
};

/* --- Hilfen ------------------------------------------------------------ */

const $ = (id) => document.getElementById(id);

async function hole(pfad, optionen) {
  const resp = await fetch(api(pfad), optionen);
  if (!resp.ok) {
    let fehler = 'error.generic';
    try {
      const daten = await resp.json();
      fehler = daten.error_key || fehler;
    } catch (e) { /* Antwort ohne JSON – Schlüssel bleibt allgemein */ }
    throw new Error(fehler);
  }
  return resp.json();
}

function sende(pfad, daten, methode = 'POST') {
  return hole(pfad, {
    method: methode,
    headers: { 'Content-Type': 'application/json' },
    body: daten ? JSON.stringify(daten) : undefined,
  });
}

function el(tag, klasse, text) {
  const knoten = document.createElement(tag);
  if (klasse) knoten.className = klasse;
  if (text !== undefined) knoten.textContent = text;
  return knoten;
}

/* --- Start ------------------------------------------------------------- */

async function start() {
  await I18n.init();
  fuelleSprachwahl();
  verdrahteReiter();
  verdrahteAnmeldung();
  verdrahteEinstellungen();

  await aktualisiere();
  Zustand.timer = setInterval(aktualisiere, 15000);
}

async function aktualisiere() {
  try {
    Zustand.status = await hole('status');
    Format.units = Zustand.status.units;
    Format.timeFormat = Zustand.status.time_format;

    const angemeldet = Zustand.status.apple.state === 'ready';
    zeigeAnmeldung(!angemeldet);

    if (angemeldet) {
      Zustand.geraete = await hole('devices');
      if (!Zustand.zonen.length) {
        try { Zustand.zonen = await hole('zones'); } catch (err) { /* später */ }
      }
      // Erst nach dem Laden zeichnen - sonst zählt die Statusleiste
      // beim ersten Durchlauf null Geräte.
      zeichneStatus(true);
      zeichneUebersicht();
      // Geräte-, Zonen- und Einstellungsansicht bewusst NICHT im Takt neu
      // zeichnen: das sind Formulare. Ein halb eingetippter Name oder eine
      // gerade geöffnete Auswahlliste wären sonst alle 15 Sekunden weg.
      if (Zustand.ansicht === 'protokoll') await zeichneProtokoll();
    }
    if (!angemeldet) zeichneStatus(false);
    zeigeMqtt();
  } catch (err) {
    console.error('Aktualisierung fehlgeschlagen', err);
  }
}

/* --- Statusleiste ------------------------------------------------------ */

/* Sagt in einer Zeile, ob die App arbeitet, wie viele Geräte sie verfolgt
   und wann sie zuletzt bei Apple war. Der Schalter dafür stand vorher
   unbeschriftet neben der Sprachwahl - dort las ihn niemand als das, was
   er ist: der Hauptschalter der Ortung. */
function zeichneStatus(sichtbar) {
  const leiste = $('statusleiste');
  leiste.classList.toggle('versteckt', !sichtbar);
  if (!sichtbar) return;

  const angehalten = Zustand.status.paused;
  leiste.classList.toggle('angehalten', angehalten);

  $('status-text').textContent =
    I18n.t(angehalten ? 'status.tracking_paused' : 'status.tracking_active');

  const verfolgt = Zustand.geraete.filter((g) => g.config.modus === 'track').length;
  const teile = [I18n.t('status.devices_tracked', { count: verfolgt })];
  if (angehalten) {
    teile.push(I18n.t('status.paused_hint'));
  } else {
    teile.push(Zustand.status.last_cycle
      ? I18n.t('status.last_poll', { when: Format.ago(Zustand.status.last_cycle) })
      : I18n.t('status.never_polled'));
  }
  $('status-rest').textContent = teile.join(' · ');

  $('status-schalter').textContent =
    I18n.t(angehalten ? 'status.resume' : 'status.pause');
}

/* --- Sprache ----------------------------------------------------------- */

function fuelleSprachwahl() {
  for (const ziel of [$('sprachwahl'), $('einst-sprache')]) {
    if (!ziel) continue;
    ziel.innerHTML = '';
    for (const sprache of I18n.languages) {
      const option = el('option', null,
        sprache.complete < 100
          ? `${sprache.name} (${sprache.complete} %)`
          : sprache.name);
      option.value = sprache.code;
      if (sprache.code === I18n.lang) option.selected = true;
      ziel.appendChild(option);
    }
  }
  $('sprachwahl').onchange = (ev) => wechsleSprache(ev.target.value);
}

async function wechsleSprache(code) {
  await sende('settings', { sprache: code });
  await I18n.load(code);
  fuelleSprachwahl();
  await aktualisiere();
}

/* --- Navigation -------------------------------------------------------- */

function verdrahteReiter() {
  document.querySelectorAll('.reiter-knopf').forEach((knopf) => {
    knopf.onclick = () => zeigeAnsicht(knopf.dataset.ansicht);
  });
  $('aktualisieren').onclick = async () => {
    await sende('poll');
    await aktualisiere();
  };
  $('status-schalter').onclick = async () => {
    await sende('tracking', { paused: !Zustand.status.paused });
    await aktualisiere();
  };
}

async function zeigeAnsicht(name) {
  Zustand.ansicht = name;
  document.querySelectorAll('.reiter-knopf').forEach((k) =>
    k.classList.toggle('aktiv', k.dataset.ansicht === name)
  );
  document.querySelectorAll('.ansicht').forEach((a) =>
    a.classList.toggle('versteckt', a.id !== `ansicht-${name}`)
  );
  if (name === 'geraete') await zeichneGeraete();
  if (name === 'zonen') await zeichneZonen();
  if (name === 'protokoll') await zeichneProtokoll();
  if (name === 'einstellungen') await ladeEinstellungen();
}

/* --- Anmeldung --------------------------------------------------------- */

function zeigeAnmeldung(sichtbar) {
  // Ein zweites Konto wird aus den Einstellungen heraus hinzugefügt - dann
  // ist die Anmeldung ein Zwischenschritt und kein Sperrbildschirm.
  const freiwillig = Zustand.kontoHinzufuegen;
  $('anmelden-abbruch').classList.toggle('versteckt', !freiwillig);
  $('anmeldung').classList.toggle('versteckt', !sichtbar && !freiwillig);
  if (freiwillig) sichtbar = true;
  document.querySelector('nav').classList.toggle('versteckt', sichtbar);
  document.querySelectorAll('.ansicht').forEach((a) => {
    if (sichtbar) a.classList.add('versteckt');
  });
  if (!sichtbar) {
    document.getElementById(`ansicht-${Zustand.ansicht}`)
      ?.classList.remove('versteckt');
    return;
  }
  const zweiFaktor = Zustand.status?.apple.state === 'twofactor';
  $('anmelde-formular').classList.toggle('versteckt', zweiFaktor);
  $('code-formular').classList.toggle('versteckt', !zweiFaktor);
  const fehler = Zustand.status?.apple.error_key;
  zeigeFehler(fehler);
}

function zeigeFehler(schluessel) {
  const feld = $('anmelde-fehler');
  feld.classList.toggle('versteckt', !schluessel);
  if (schluessel) feld.textContent = I18n.t(schluessel);
}

function verdrahteAnmeldung() {
  $('anmelden').onclick = async () => {
    zeigeFehler(null);
    $('anmelden').disabled = true;
    try {
      Zustand.status = { apple: await sende('account/login', {
        username: $('apple-id').value,
        password: $('apple-passwort').value,
        store_password: $('passwort-merken').checked,
      }) };
      $('apple-passwort').value = '';
      if (Zustand.status?.apple?.state === 'ready') {
        Zustand.kontoHinzufuegen = false;
      }
      await aktualisiere();
    } catch (err) {
      zeigeFehler(err.message);
    } finally {
      $('anmelden').disabled = false;
    }
  };

  $('code-senden').onclick = async () => {
    zeigeFehler(null);
    try {
      await sende('account/2fa', {
        code: $('code').value,
        trust: $('vertrauen').checked,
        username: Zustand.status?.apple?.username,
      });
      $('code').value = '';
      Zustand.kontoHinzufuegen = false;
      await aktualisiere();
    } catch (err) {
      zeigeFehler(err.message);
    }
  };

  $('code-neu').onclick = () => sende('account/resend');

  $('code').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') $('code-senden').click();
  });
}

/* --- Übersicht --------------------------------------------------------- */

function zeichneUebersicht() {
  const ziel = $('geraete-kacheln');
  ziel.innerHTML = '';

  const verfolgt = Zustand.geraete.filter((g) => g.config.modus === 'track');
  $('keine-geraete').classList.toggle('versteckt', Zustand.geraete.length > 0);

  for (const geraet of verfolgt) {
    const kachel = el('div', `kachel zustand-${geraet.state}`);

    const kopf = el('div', 'kachel-kopf');
    kopf.appendChild(el('span', 'kachel-name', geraet.name));
    if (geraet.battery !== null && geraet.battery !== undefined) {
      const akku = el('span', 'akku', `${geraet.battery} %`);
      if (geraet.battery <= 20) akku.classList.add('akku-schwach');
      kopf.appendChild(akku);
    }
    kachel.appendChild(kopf);

    // 'In Zuhause' liest sich falsch - die Heimzone hat einen eigenen
    // Zustandstext, jede andere Zone wird eingesetzt.
    const zustand = el('div', 'kachel-zustand',
      geraet.state === 'in_zone' && geraet.zone_name
        ? I18n.t('state.in_zone', { zone: geraet.zone_name })
        : I18n.t(`state.${geraet.state}`));
    kachel.appendChild(zustand);

    if (geraet.address?.display) {
      kachel.appendChild(el('div', 'kachel-adresse', geraet.address.display));
    }

    // Fahrtrichtung nur zeigen, wenn sie etwas aussagt - "Unbekannt" oder
    // "Steht" steht schon im Zustand darüber.
    if (geraet.heading === 'towards' || geraet.heading === 'away') {
      kachel.appendChild(el('div', 'kachel-richtung',
        I18n.t(`heading.${geraet.heading}`, { zone: I18n.t('zone.home') })));
    }

    const zeilen = el('div', 'kachel-werte');
    if (geraet.distance_m !== null && geraet.distance_m !== undefined
        && geraet.state !== 'home') {
      zeilen.appendChild(wertZeile('sensor.distance',
        Format.distance(geraet.distance_m)));
    }
    if (geraet.travel?.duration_seconds) {
      zeilen.appendChild(wertZeile('sensor.travel_time',
        Format.duration(geraet.travel.duration_seconds)));
      zeilen.appendChild(wertZeile('sensor.arrival_time',
        Format.time(geraet.travel.arrival_timestamp)));
    }
    if (geraet.speed_ms > 1) {
      zeilen.appendChild(wertZeile('sensor.speed', Format.speed(geraet.speed_ms)));
    }
    if (geraet.reference) {
      const r = geraet.reference;
      zeilen.appendChild(wertZeile('sensor.distance_zone',
        Format.distance(r.distance_m), { zone: r.zone_name }));
      if (r.travel?.duration_seconds) {
        zeilen.appendChild(wertZeile('sensor.arrival_time_zone',
          Format.time(r.travel.arrival_timestamp), { zone: r.zone_name }));
      }
    }
    zeilen.appendChild(wertZeile('device.last_seen', Format.ago(geraet.last_seen)));
    kachel.appendChild(zeilen);

    const fuss = el('div', 'kachel-fuss');
    fuss.appendChild(el('span', 'quelle',
      I18n.t(geraet.source === 'companion'
        ? 'device.source_companion'
        : geraet.source === 'icloud' ? 'device.source_icloud'
        : 'device.source_none')));
    if (geraet.interval) {
      fuss.appendChild(el('span', 'takt', Format.duration(geraet.interval)));
    }
    kachel.appendChild(fuss);

    ziel.appendChild(kachel);
  }
}

function wertZeile(schluessel, wert, params) {
  const zeile = el('div', 'wert-zeile');
  zeile.appendChild(el('span', 'wert-name', I18n.t(schluessel, params)));
  zeile.appendChild(el('span', 'wert', wert));
  return zeile;
}

/* --- Geräte ------------------------------------------------------------ */

async function zeichneGeraete() {
  if (!Zustand.tracker.length) {
    try {
      Zustand.tracker = await hole('trackers');
    } catch (err) { Zustand.tracker = []; }
  }

  const ziel = $('geraete-liste');
  ziel.innerHTML = '';

  // Ein Apple-Konto bringt schnell zwei Dutzend Geräte mit, von denen die
  // meisten nie verfolgt werden sollen. Nach oben kommt deshalb, was man
  // tatsächlich anfasst: schon Verfolgtes, dann Geräte, die man am Körper
  // trägt, dann der Rest.
  const RANG = { iPhone: 0, iPad: 1, Watch: 2, AirPods: 3, Mac: 4 };
  const sortiert = [...Zustand.geraete].sort((a, b) => {
    const aktiv = (g) => (g.config.modus === 'track' ? 0 : 1);
    if (aktiv(a) !== aktiv(b)) return aktiv(a) - aktiv(b);
    const rang = (g) => RANG[g.device_class] ?? 9;
    if (rang(a) !== rang(b)) return rang(a) - rang(b);
    return (a.name || '').localeCompare(b.name || '', I18n.lang);
  });

  for (const geraet of sortiert) {
    // Zusammenklappbar: Bei zwei Dutzend Geräten im Konto ist die
    // aufgeklappte Liste unbenutzbar lang. Zugeklappt genügen Punkt, Name
    // und Modell, um das gesuchte Gerät zu finden.
    const karte = el('details', 'karte geraet-karte');
    karte.open = Zustand.offeneGeraete.has(geraet.id);
    karte.addEventListener('toggle', () => {
      if (karte.open) Zustand.offeneGeraete.add(geraet.id);
      else Zustand.offeneGeraete.delete(geraet.id);
    });

    const kopf = el('summary', 'geraet-kopf');
    // Der Punkt zeigt die Betriebsart, ohne dass man aufklappen muss.
    const punkt = el('span', `betriebspunkt modus-${geraet.config.modus}`);
    punkt.title = I18n.t(`device.mode_${geraet.config.modus}`);
    kopf.appendChild(punkt);
    kopf.appendChild(el('strong', 'geraet-name', geraet.name));
    kopf.appendChild(el('span', 'modell', geraet.model || ''));
    karte.appendChild(kopf);

    // Betriebsart
    const modusFeld = el('div', 'feld');
    modusFeld.appendChild(el('label', null, I18n.t('device.mode')));
    const auswahl = el('select');
    for (const modus of ['track', 'monitor', 'inactive']) {
      const option = el('option', null, I18n.t(`device.mode_${modus}`));
      option.value = modus;
      if (geraet.config.modus === modus) option.selected = true;
      auswahl.appendChild(option);
    }
    auswahl.onchange = async () => {
      await sende(`devices/${encodeURIComponent(geraet.id)}`,
        { modus: auswahl.value });
      await aktualisiere();
      await zeichneGeraete();
    };
    modusFeld.appendChild(auswahl);
    modusFeld.appendChild(el('p', 'hinweis klein',
      I18n.t(`device.mode_${geraet.config.modus}_hint`)));
    karte.appendChild(modusFeld);

    // Anzeigename
    const nameFeld = el('div', 'feld');
    nameFeld.appendChild(el('label', null, I18n.t('device.name')));
    const nameEingabe = el('input');
    nameEingabe.type = 'text';
    nameEingabe.value = geraet.config.name || '';
    nameEingabe.placeholder = geraet.name;
    nameEingabe.onchange = () =>
      sende(`devices/${encodeURIComponent(geraet.id)}`,
        { name: nameEingabe.value || null });
    nameFeld.appendChild(nameEingabe);
    karte.appendChild(nameFeld);

    // Companion-Tracker
    const companionFeld = el('div', 'feld');
    companionFeld.appendChild(el('label', null, I18n.t('device.companion_entity')));
    const companionWahl = el('select');
    const leer = el('option', null, I18n.t('common.none'));
    leer.value = '';
    companionWahl.appendChild(leer);
    for (const tracker of Zustand.tracker) {
      const option = el('option', null, tracker.name);
      option.value = tracker.entity_id;
      if (geraet.config.companion_entity === tracker.entity_id) {
        option.selected = true;
      }
      companionWahl.appendChild(option);
    }
    companionWahl.onchange = () =>
      sende(`devices/${encodeURIComponent(geraet.id)}`,
        { companion_entity: companionWahl.value || null });
    companionFeld.appendChild(companionWahl);
    companionFeld.appendChild(el('p', 'hinweis klein',
      I18n.t('device.companion_hint')));
    karte.appendChild(companionFeld);

    // Zweite Bezugszone
    const zonenFeld = el('div', 'feld');
    zonenFeld.appendChild(el('label', null, I18n.t('device.reference_zone')));
    const zonenWahl = el('select');
    const keine = el('option', null, I18n.t('common.none'));
    keine.value = '';
    zonenWahl.appendChild(keine);
    for (const zone of Zustand.zonen) {
      if (zone.entity_id === 'zone.home') continue;
      const option = el('option', null, zone.name);
      option.value = zone.entity_id;
      if (geraet.config.reference_zone === zone.entity_id) option.selected = true;
      zonenWahl.appendChild(option);
    }
    zonenWahl.onchange = async () => {
      await sende(`devices/${encodeURIComponent(geraet.id)}`,
        { reference_zone: zonenWahl.value || null });
      await sende('poll');
    };
    zonenFeld.appendChild(zonenWahl);
    zonenFeld.appendChild(el('p', 'hinweis klein',
      I18n.t('device.reference_zone_hint')));
    karte.appendChild(zonenFeld);

    // Zeitversatz für Geräte in einer anderen Zeitzone
    const zeitFeld = el('div', 'feld');
    zeitFeld.appendChild(el('label', null, I18n.t('device.timezone_offset')));
    const zeitWahl = el('select');
    for (let h = -12; h <= 14; h += 1) {
      const option = el('option', null,
        h === 0 ? I18n.t('device.timezone_none') : (h > 0 ? `+${h} h` : `${h} h`));
      option.value = String(h);
      if ((geraet.config.timezone_offset || 0) === h) option.selected = true;
      zeitWahl.appendChild(option);
    }
    zeitWahl.onchange = () =>
      sende(`devices/${encodeURIComponent(geraet.id)}`,
        { timezone_offset: Number(zeitWahl.value) });
    zeitFeld.appendChild(zeitWahl);
    zeitFeld.appendChild(el('p', 'hinweis klein',
      I18n.t('device.timezone_offset_hint')));
    karte.appendChild(zeitFeld);

    // Befehle ans Gerät
    if (geraet.config.modus !== 'inactive') {
      const befehle = el('div', 'befehle');
      for (const [befehl, label] of [['locate', 'control.locate'],
                                     ['sound', 'control.play_sound'],
                                     ['message_send', 'control.send_message']]) {
        const knopf = el('button', 'knopf still', I18n.t(label));
        knopf.onclick = () => sendeBefehl(geraet, befehl);
        befehle.appendChild(knopf);
      }
      const verloren = el('button', 'knopf gefahr-knopf', I18n.t('control.lost_mode'));
      verloren.onclick = () => zeigeVerloren(geraet);
      befehle.appendChild(verloren);
      karte.appendChild(befehle);
    }

    ziel.appendChild(karte);
  }

  if (!Zustand.geraete.length) {
    ziel.appendChild(el('p', 'leer', I18n.t('device.no_devices')));
  }
}

/* --- Befehle ----------------------------------------------------------- */

async function sendeBefehl(geraet, befehl) {
  const daten = { befehl };
  if (befehl === 'message_send') {
    const text = prompt(I18n.t('control.message_text'),
                        I18n.t('control.message_default'));
    if (text === null) return;
    daten.text = text;
  }
  try {
    const antwort = await sende(
      `devices/${encodeURIComponent(geraet.id)}/command`, daten);
    melde(antwort.ok ? I18n.t('control.sent')
                     : I18n.t(antwort.error_key || 'error.generic'));
  } catch (err) {
    melde(I18n.t(err.message));
  }
}

function zeigeVerloren(geraet) {
  $('verloren-geraet').textContent = geraet.name;
  $('verloren-text').value = I18n.t('control.message_default');
  $('verloren-dialog').classList.remove('versteckt');
  $('verloren-ab').onclick = async () => {
    const nummer = $('verloren-nummer').value.trim();
    if (!nummer) return;
    try {
      const antwort = await sende(
        `devices/${encodeURIComponent(geraet.id)}/command`,
        { befehl: 'lost', nummer, text: $('verloren-text').value });
      melde(antwort.ok ? I18n.t('control.sent')
                       : I18n.t(antwort.error_key || 'error.generic'));
    } catch (err) {
      melde(I18n.t(err.message));
    }
    $('verloren-dialog').classList.add('versteckt');
  };
  $('verloren-zurueck').onclick = () =>
    $('verloren-dialog').classList.add('versteckt');
}

/* Kurze Rückmeldung, die von selbst wieder verschwindet. */
function melde(text) {
  const alt = document.querySelector('.meldung');
  if (alt) alt.remove();
  const knoten = el('div', 'meldung', text);
  document.body.appendChild(knoten);
  setTimeout(() => knoten.remove(), 3000);
}

/* --- Zonen ------------------------------------------------------------- */

async function zeichneZonen() {
  try {
    Zustand.zonen = await hole('zones');
  } catch (err) {
    Zustand.zonen = [];
  }
  const ziel = $('zonen-liste');
  ziel.innerHTML = '';

  if (!Zustand.zonen.length) {
    ziel.appendChild(el('p', 'leer', I18n.t('zone.no_zones')));
    return;
  }

  for (const zone of Zustand.zonen) {
    const zeile = el('div', 'zonen-zeile');
    zeile.appendChild(el('span', 'zonen-name', zone.name));
    zeile.appendChild(el('span', 'zonen-id', zone.entity_id));
    zeile.appendChild(el('span', 'zonen-radius',
      Format.distance(zone.radius)));
    ziel.appendChild(zeile);
  }
}

/* --- Protokoll --------------------------------------------------------- */

async function zeichneProtokoll() {
  const stufe = $('protokoll-stufe').value || 'info';
  let eintraege = [];
  try {
    eintraege = await hole(`events?lang=${I18n.lang}&level=${stufe}&limit=200`);
  } catch (err) { /* leer lassen */ }

  const ziel = $('protokoll-liste');
  ziel.innerHTML = '';

  if (!eintraege.length) {
    ziel.appendChild(el('p', 'leer', I18n.t('event.empty')));
    return;
  }

  for (const eintrag of eintraege) {
    const zeile = el('div', `protokoll-zeile stufe-${eintrag.level}`);
    zeile.appendChild(el('span', 'protokoll-zeit', Format.dateTime(eintrag.ts)));
    zeile.appendChild(el('span', 'protokoll-text', eintrag.text));
    ziel.appendChild(zeile);
  }
}

/* --- Einstellungen ----------------------------------------------------- */

async function ladeEinstellungen() {
  Zustand.einstellungen = await hole('settings');
  const e = Zustand.einstellungen;

  $('einst-sprache').value = e.sprache;
  $('einst-einheiten').value = e.einheiten;
  $('einst-zeitformat').value = e.zeitformat;
  $('int-zuhause').value = e.intervalle.zuhause;
  $('int-abwesend').value = e.intervalle.abwesend;
  $('int-unterwegs').value = e.intervalle.unterwegs;
  $('int-zonennaehe').value = e.intervalle.zonennaehe;
  $('einst-genauigkeit').value = e.genauigkeitsschwelle;
  $('einst-geocode').value = e.geocode.anbieter;
  $('einst-route').value = e.route.anbieter;
  $('einst-praefix').value = e.entity_praefix;
  if (!Zustand.zonen.length) {
    try { Zustand.zonen = await hole('zones'); } catch (err) { /* egal */ }
  }
  zeichneKonten();
  zeichneFahrten();
  zeigeMqtt();
}

function zeigeMqtt() {
  const feld = $('mqtt-status');
  if (!feld || !Zustand.status) return;
  const mqtt = Zustand.status.mqtt;
  feld.textContent = mqtt.connected
    ? I18n.t('event.mqtt_connected', { host: mqtt.host })
    : I18n.t('error.mqtt_unavailable');
  feld.classList.toggle('fehler', !mqtt.connected);
}

function zeichneKonten() {
  const ziel = $('kontenliste');
  if (!ziel) return;
  ziel.innerHTML = '';
  const konten = Zustand.status?.apple?.accounts || [];
  if (!konten.length) {
    ziel.appendChild(el('p', 'hinweis', I18n.t('account.none')));
    return;
  }
  for (const konto of konten) {
    const zeile = el('div', 'konto-zeile');
    zeile.appendChild(el('span', 'konto-name', konto.username || '?'));
    const klasse = { ready: 'bereit', twofactor: 'wartet', error: 'fehler' }[konto.state] || '';
    // Eigene Kurztexte statt der Sätze aus dem Anmeldedialog: dort steht
    // "Verbunden als {user}", und der Name steht hier schon daneben.
    zeile.appendChild(el('span', `konto-zustand ${klasse}`,
      I18n.t({ ready: 'account.state_ready',
               twofactor: 'account.state_waiting',
               error: 'account.state_error' }[konto.state]
             || 'account.state_offline')));
    const weg = el('button', 'knopf still klein-knopf', I18n.t('common.delete'));
    weg.onclick = async () => {
      if (!confirm(I18n.t('account.remove_confirm', { user: konto.username }))) return;
      await hole(`accounts/${encodeURIComponent(konto.username)}`,
                 { method: 'DELETE' });
      await aktualisiere();
      await ladeEinstellungen();
    };
    zeile.appendChild(weg);
    ziel.appendChild(zeile);
  }
}

function zeichneFahrten() {
  const ziel = $('fahrtenliste');
  if (!ziel) return;
  ziel.innerHTML = '';
  const trips = Zustand.status?.trips;
  if (!trips || !trips.fahrten) {
    ziel.appendChild(el('p', 'hinweis', I18n.t('trips.empty')));
    return;
  }
  ziel.appendChild(el('p', 'hinweis',
    I18n.t('trips.count', { count: trips.fahrten })));
  for (const eintrag of trips.ziele || []) {
    const zeile = el('div', 'fahrt-zeile');
    const zone = Zustand.zonen.find((z) => z.entity_id === eintrag.ziel);
    zeile.appendChild(el('span', 'fahrt-ziel',
      `${zone ? zone.name : eintrag.ziel} · ${I18n.t(`trips.window_${eintrag.fenster}`)}`));
    zeile.appendChild(el('span', 'fahrt-wert',
      `${eintrag.anzahl} × · ${I18n.t('trips.factor', { factor: eintrag.faktor })}`));
    ziel.appendChild(zeile);
  }
}

function verdrahteEinstellungen() {
  $('konto-hinzu').onclick = async () => {
    Zustand.kontoHinzufuegen = true;
    $('apple-id').value = '';
    $('apple-passwort').value = '';
    await aktualisiere();
  };
  $('anmelden-abbruch').onclick = async () => {
    Zustand.kontoHinzufuegen = false;
    await aktualisiere();
  };

  $('einstellungen-speichern').onclick = async () => {
    const sprache = $('einst-sprache').value;
    const spracheVorher = I18n.lang;

    await sende('settings', {
      sprache,
      einheiten: $('einst-einheiten').value,
      zeitformat: $('einst-zeitformat').value,
      entity_praefix: $('einst-praefix').value,
      genauigkeitsschwelle: Number($('einst-genauigkeit').value),
      intervalle: {
        zuhause: Number($('int-zuhause').value),
        abwesend: Number($('int-abwesend').value),
        unterwegs: Number($('int-unterwegs').value),
        zonennaehe: Number($('int-zonennaehe').value),
      },
      geocode: { anbieter: $('einst-geocode').value },
      route: { anbieter: $('einst-route').value },
    });

    if (sprache !== spracheVorher) {
      await I18n.load(sprache);
      fuelleSprachwahl();
    }
    await aktualisiere();

    const hinweis = $('speicher-hinweis');
    hinweis.classList.remove('versteckt');
    setTimeout(() => hinweis.classList.add('versteckt'), 2000);
  };

  $('abmelden').onclick = async () => {
    await sende('account/logout', { forget: true });
    await aktualisiere();
  };

  $('zuruecksetzen').onclick = async () => {
    if (!confirm(I18n.t('settings.reset_confirm'))) return;
    await sende('reset');
    location.reload();
  };

  $('protokoll-stufe').onchange = zeichneProtokoll;
  $('protokoll-leeren').onclick = async () => {
    await hole('events', { method: 'DELETE' });
    await zeichneProtokoll();
  };
}

document.addEventListener('DOMContentLoaded', start);
