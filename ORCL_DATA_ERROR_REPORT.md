# Bericht: Datenfehler ORCL (Oracle Corporation)

## Zusammenfassung
Die Analyse der Oracle-Daten (`ORCL`) hat ergeben, dass das System fälschlicherweise Daten für **`ORCX`** (Defiance Daily Target 2X Long ORCL ETF) herunterlädt und speichert. Dies führt zu massiven Abweichungen im Chart, da ein 2x gehebelter ETF eine völlig andere Volatilität und Preisstruktur aufweist als die zugrunde liegende Aktie.

## Identifizierter Fehler
In der Datei `config/ticker_map.json` ist der Ticker `ORCL` wie folgt konfiguriert:
```json
"ORCL": {
    "symbol": "ORCX",
    "exchange": "NASDAQ",
    "currency": "USD",
    "sec_type": "STK"
}
```
Dieser Eintrag ist falsch. Die Oracle-Aktie sollte das Symbol `ORCL` auf der `NYSE` verwenden.

### Auswirkungen
- **Falscher Preis**: Die Preise im `ORCL/1D.parquet` entsprechen dem gehebelten ETF.
- **"Gap Up" im März 2026**: Die gemeldeten Preissprünge resultieren aus der 2-fachen täglichen Hebelwirkung des Produkts `ORCX`, die besonders bei Marktvolatilität im März 2026 zu starken Abweichungen vom Original-Chart führte.
- **Andere Ticker**: Auch `ORC` (Orchid Island Capital) wurde fälschlicherweise auf `ORCX` gemappt.

## Ursachenanalyse (Systematisch)
Die Ursache liegt in der automatischen Vertragserkennung (**Auto-Discovery**) in Kombination mit der Konfiguration der Börsenprioritäten:

1.  **Exchange-Priorität**: In `config/auto_discovery.json` ist die `NASDAQ` vor der `NYSE` priorisiert.
2.  **Namensübereinstimmung**: Bei einer Suche nach "ORCL" gibt Interactive Brokers (IBKR) sowohl die Aktie (`ORCL` auf NYSE) als auch ähnliche Namen (wie `ORCX` auf NASDAQ) zurück.
3.  **Fehlerhafte Auswahl**: Da `ORCX` auf der NASDAQ gelistet ist (Priorität 2) und die Oracle-Aktie auf der NYSE (Priorität 3), entscheidet sich der Algorithmus in `src/downloader.py` für den falschen Treffer, da NASDAQ in der Liste weiter vorne steht.

## Vergleich mit Yahoo Finance (Referenz)
Ein Vergleich mit den offiziellen Yahoo Finance Daten bestätigt:
- **ORCL (Yahoo)**: Stabilere Preisbewegung, Notierung an der NYSE.
- **ORCL (Lokal/ORCX)**: Extreme Volatilität (2x Hebel), Notierung an der NASDAQ.
- Die lokalen Daten zeigen Kursbewegungen, die exakt das Doppelte der täglichen Prozentbewegung von Oracle widerspiegeln (typisch für ORCX).

## Lösungsvorschlag

### 1. Sofortmaßnahme (Manuell)
Korrektur der Einträge in `config/ticker_map.json` für alle betroffenen Ticker:
```json
"ORCL": {
    "symbol": "ORCL",
    "exchange": "NYSE",
    "currency": "USD",
    "sec_type": "STK"
}
```

### 2. Systematische Korrektur (Konfiguration)
In `config/auto_discovery.json` sollte die Konfiguration der Börsenprioritäten überprüft und ggf. angepasst werden:
```json
"exchange_priority": ["SMART", "IBIS2", "NYSE", "NASDAQ", "LSEETF"]
```
*(NYSE vor NASDAQ, wenn hauptsächlich US-Blue-Chips gehandelt werden).*

### 3. Systematische Korrektur (Code)
Anpassung der Scoring-Logik in `src/downloader.py`. Der Algorithmus sollte Treffer, bei denen das gefundene Symbol **exakt** mit dem gesuchten Ticker übereinstimmt, am höchsten priorisieren, noch bevor die Börsenpriorität greift.

```python
# Empfohlener Pseudo-Code für downloader.py:
def score(desc):
    # 1. Prio: Exakter Ticker-Match
    ticker_match = 0 if desc.contract.symbol == search_ticker else 1
    # 2. Prio: Währung
    # 3. Prio: Börse
    return (ticker_match, curr_score, exch_score)
```

---
*Erstellt am 13. April 2026*
