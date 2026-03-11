================================================================================
TUTORIAL: STOCK-DATA-NODE (IN DOCKER)
================================================================================

Hallo Zukunfts-Daniel! 
Hier ist eine kurze Erinnerung, wie dieses Projekt funktioniert und wie man
es nach einem Jahr wieder ans Laufen bekommt.

--------------------------------------------------------------------------------
1. VORAUSSETZUNGEN
--------------------------------------------------------------------------------
- Docker und Docker Compose (oder Portainer) sind installiert.
- Ein IBKR Gateway (oder TWS) läuft auf der gleichen Maschine oder im Netzwerk.

--------------------------------------------------------------------------------
2. KONFIGURATION ANPASSEN
--------------------------------------------------------------------------------
WICHTIG für Portainer: Die `docker-compose.yml` nutzt ABSOLUTE PFADE 
(z.B. `/home/daniel/stock-data-node/...`), weil Portainer bei Git-Deployments 
ansonsten gerne temporäre Ordner für Volumes erzeugt. 
-> Falls sich dein Ordner ändert (z.B. von `/daniel/` zu was anderem), musst 
   du die Pfade in der `docker-compose.yml` anpassen!

Gateway IP/Port:
Die IP und der Port des IB Gateways werden in der `config/gateway.json` 
eingestellt. Da der Container im "host" Netzwerk-Modus läuft (network_mode: "host"), 
kannst du das lokale Gateway einfach unter 127.0.0.1 (oder 0.0.0.0) ansprechen.

--------------------------------------------------------------------------------
3. STARTEN
--------------------------------------------------------------------------------
Gehe ins Verzeichnis und starte den Container:
  docker compose up -d --build

Alternativ: In Portainer den "Stack" updaten/redeployen.

Tipp: Um die Logs live zu sehen:
  docker logs -f stock-data-node

--------------------------------------------------------------------------------
4. TICKER HERUNTERLADEN (DAS WICHTIGSTE)
--------------------------------------------------------------------------------
Es gibt einen "File Watcher", der den Ordner `/watch` überwacht.

Schritt 1: 
Erstelle einfach eine Textdatei im `watch/` Ordner, z.B. `meine_ticker.txt`.

Schritt 2: 
Trage dort deine gewünschten Ticker ein (getrennt durch Leerzeichen oder Komma).
Beispiel:
  AAPL MSFT GOOG

Schritt 3:
Der Container erkennt die Datei sofort, liest sie ein und verschiebt sie nach watchlists/. und löscht die ursprüngliche Datei.
Er reiht die Ticker automatisch in die Download-Warteschlange ein. 


--------------------------------------------------------------------------------
5. DIE OPTIONALE TICKER MAP (ticker_map.json)
--------------------------------------------------------------------------------
US-Aktien: 
Du MUSST US-Aktien NICHT vorher in der `config/ticker_map.json` eintragen! 
Wenn du "AAPL" in den Watch-Ordner wirfst, geht das System automatisch von einer 
US-Aktie aus (SMART, USD, STK) und lädt sie runter.

Andere Börsen / Währungen / Aliasse:
Wenn du z.B. eine europäische Aktie laden willst oder einen Alias vergeben willst 
(z.B. google = GOOGL), trägst du das in die `config/ticker_map.json` ein:
  "google": {"symbol": "GOOGL", "exchange": "SMART", "currency": "USD", "sec_type": "STK"}

--------------------------------------------------------------------------------
6. FEHLERBEHEBUNG (Blacklist)
--------------------------------------------------------------------------------
Was passiert, wenn IBKR einen Ticker nicht kennt (z.B. Tippfehler)?
-> Der Ticker wird von IBKR abgelehnt.
-> Das Skript schreibt ihn in die Datei `state/failed_ticker.json` (Blacklist).
-> Außerdem wird in der `ticker_map.json` ein ignoriert-Verweis ("SKIP") gesetzt.

Wie entsperre ich einen Ticker?
Falls er dauerhaft gesperrt ist, öffne die `state/failed_ticker.json` und lösche 
den Eintrag. (Oder behebe den Fehler in der ticker_map, das nächste Mal, wenn 
ein Download für diesen Ticker erfolgreich ist, löscht das Script ihn automatisch 
wieder von der Blacklist!).

--------------------------------------------------------------------------------
7. TICKER UPDATES (Staleness Checks)
--------------------------------------------------------------------------------
Das System hat einen internen Timer, um fehlende Daten automatisch nachzuladen. 
Du kannst diesen Scan aber auch jederzeit manuell über die API auslösen:

Befehl (Alle Ticker updaten):
  curl -X POST http://localhost:8002/trigger-staleness

Dies prüft alle Parquet-Dateien und lädt die neuesten Kerzen nach.

--------------------------------------------------------------------------------
8. FEATURE-BERECHNUNG (Features manuell auslösen)
--------------------------------------------------------------------------------
Nachdem die Daten heruntergeladen wurden, berechnet das System normalerweise 
automatisch technische Indikatoren (RS-Line, Bollinger Bands, EMA, etc.). 
Du kannst diesen Prozess aber auch manuell über die API triggern:

Befehl (Manuelle Feature-Berechnung):
  curl -X POST http://localhost:8002/features/calculate

Dies startet die Berechnung im Hintergrund. Falls bereits eine Berechnung läuft, 
antwortet die API mit einem Fehler (409 Conflict).

--------------------------------------------------------------------------------
10. PORT-PROBLEME (Port 8002 belegt?)
--------------------------------------------------------------------------------
Falls beim Start die Fehlermeldung "address already in use" erscheint, 
läuft wahrscheinlich noch ein alter Prozess im Hintergrund. 
Du kannst ihn mit diesem Skript automatisch beenden:

Befehl (Port freigeben):
  ./.venv/bin/python src/kill_server.py

Das Skript liest automatisch den Port aus der `config/gateway.json` 
und beendet den belegenden Prozess.

--------------------------------------------------------------------------------
11. PERMISSION
--------------------------------------------------------------------------------

sudo chmod -R 777 /home/daniel/stock-data-node/data/parquet

--------------------------------------------------------------------------------
12. PERFORMANCE TUNING (F-OPT-060, F-OPT-070)
--------------------------------------------------------------------------------
Für optimale Bulk-Downloads empfehlen wir folgende Einstellungen:

JVM Heap-Speicher (IB Gateway):
  Setze den JVM Heap-Speicher auf mindestens 4096 MB (-Xmx4096m).
  In der IB Gateway Konfiguration oder als Docker Environment Variable:
    JAVA_HEAP_SIZE=4096

Logging-Level:
  Für Massendaten-Downloads kann das Logging-Level in config/settings.json
  auf "INFO" gesetzt werden (default), um CPU-Overhead zu minimieren:
    "bulk_log_level": "INFO"

Concurrency-Tuning:
  Die Parallelität wird dynamisch verwaltet (Dynamic Semaphore Throttling).
  Start-Werte in config/settings.json:
    "delayed_max_concurrent": 15    (für Free/Delayed Data)
    "live_max_concurrent": 20       (für Live Data)
  Bei Pacing-Fehlern wird die Parallelität automatisch reduziert und
  nach erfolgreichen Requests schrittweise wieder erhöht.

================================================================================
Viel Erfolg mein Freund!
