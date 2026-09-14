# Tests

Ohne Testrahmen, absichtlich: zwei Skripte, die je einen Weg durchspielen,
der schon einmal kaputt war.

```bash
python3 tests/test_2fa.py      # Überlebt die Anmeldung mit Code den Neustart?
python3 tests/test_logik.py    # Zonen, Zustände und Abfragetakte auf einer Fahrt
python3 tools/check_translations.py   # Kataloge gegen den Code
```

Beide Skripte brauchen `cryptography` und laufen ohne Netz, ohne Apple-Konto
und ohne Home Assistant – alle äußeren Dienste sind durch Attrappen ersetzt.
