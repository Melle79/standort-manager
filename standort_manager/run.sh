#!/usr/bin/with-contenv bashio
# with-contenv ist Pflicht: ohne sie fehlen SUPERVISOR_TOKEN und die
# Zeitzone, und das Add-on faellt auf UTC und ohne HA-Zugriff zurueck.

export ADDON_VERSION="$(bashio::addon.version)"
export LOG_LEVEL="$(bashio::config 'log_level')"
export PORT=8097
export DATA_DIR=/data
export WWW_DIR=/www
export TRANSLATIONS_DIR=/translations

# MQTT: den vom Supervisor gemeldeten Broker bevorzugen, die Optionen
# sind nur der Ausweg fuer einen Broker ausserhalb von Home Assistant.
export MQTT_ENABLED="$(bashio::config 'mqtt_enabled')"
if bashio::services.available 'mqtt'; then
  AUTO_HOST=$(bashio::services 'mqtt' 'host')
  AUTO_PORT=$(bashio::services 'mqtt' 'port')
  AUTO_USER=$(bashio::services 'mqtt' 'username')
  AUTO_PASS=$(bashio::services 'mqtt' 'password')
else
  AUTO_HOST=""; AUTO_PORT=""; AUTO_USER=""; AUTO_PASS=""
fi

CFG_HOST=$(bashio::config 'mqtt_host')
CFG_PORT=$(bashio::config 'mqtt_port')
CFG_USER=$(bashio::config 'mqtt_user')
CFG_PASS=$(bashio::config 'mqtt_password')

export MQTT_HOST="${CFG_HOST:-$AUTO_HOST}"
export MQTT_PORT="${CFG_PORT:-${AUTO_PORT:-1883}}"
export MQTT_USER="${CFG_USER:-$AUTO_USER}"
export MQTT_PASSWORD="${CFG_PASS:-$AUTO_PASS}"

export ENTITY_PRAEFIX="$(bashio::config 'entity_praefix')"
# Wohin die Dashboard-Karte kopiert wird. /config ist ueber map: config:rw
# eingehaengt.
export HA_WWW_DIR=/config/www

if [ "$MQTT_ENABLED" = "true" ] && [ -n "$MQTT_HOST" ]; then
  bashio::log.info "MQTT: ${MQTT_HOST}:${MQTT_PORT}"
else
  bashio::log.warning "Kein MQTT-Broker – es werden keine Entitaeten veroeffentlicht"
fi

bashio::log.info "Standort-Manager ${ADDON_VERSION} startet auf Port ${PORT}"

cd /app
exec python3 server.py
