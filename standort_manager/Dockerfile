ARG BUILD_FROM
FROM $BUILD_FROM

# py3-cryptography als Alpine-Paket: der pip-Build braucht sonst Rust und
# scheitert auf armv7 regelmaessig am Speicher.
RUN apk add --no-cache python3 py3-pip py3-cryptography \
    && pip3 install --no-cache-dir --break-system-packages \
        flask \
        requests \
        paho-mqtt \
        "pyicloud>=2.7.0" \
        rich \
        websocket-client

# rich steht hier, obwohl niemand es direkt benutzt: pyicloud 2.7.0
# importiert es beim Laden seines Notes-Dienstes, deklariert es aber
# nicht als Abhaengigkeit. Ohne die Zeile scheitert schon der Import.
#
# websocket-client fuer die Lovelace-Ressourcen: die Dashboard-Karte laesst
# sich nur ueber die WebSocket-API anmelden, eine REST-Schnittstelle dafuer
# gibt es nicht.

COPY app /app
COPY translations /translations
COPY www /www
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
