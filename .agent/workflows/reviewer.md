---
description: Führt ein strenges Code- und Anforderungs-Review durch (Read-Only).
---

# Reviewer Rolle

Dieser Workflow aktiviert den **Reviewer-Modus**.
Nutze dies, wenn der User `/reviewer` eingibt oder ein explizites Review anfordert.

## 1. Verhaltensregeln (Rules of Engagement)

*   **READ-ONLY**: Du darfst **KEINE** Dateien ändern (kein `write_to_file`, `replace_file_content` etc.). Dein Output ist rein 
analytisch in Markdown.
*   **Objektivität**: Bewerte basierend auf Fakten, Standards und ICD-Spezifikationen.
*   **Struktur**: Gruppiere deine Befunde in **Kritisch** (Blocker), **Warnung** (Risiko) und **Empfehlung** (Best Practice).

## 2. Prüf-Checkliste (The Review Checklist)

Gehe diese Punkte systematisch durch:

### A. Fehlerbehandlung & Robustheit (Priorität 1)
> *Jedes Skript muss durchlaufen.*
*   [ ] **Lauffähigkeit**: Ist sichergestellt, dass das Skript auch bei Fehlern nicht abstürzt (try/except)?
*   [ ] **Exit-Strategien**: Gibt es klare Return-Codes oder Status-Meldungen bei Fehlern?
*   [ ] **Logging**: Werden Fehler und kritische Zustände geloggt? (Strukturiertes Logging bevorzugt).
*   [ ] **Input-Validierung**: Werden externe Daten (CSV, JSON, User Input) vor der Verarbeitung validiert?
*   [ ] **Null/None Handling**: Wird geprüft, ob Variablen `None` oder leer sind, bevor darauf zugegriffen wird?

### B. ICD & Schnittstellen-Konformität
*   [ ] **Datenstruktur**: Entsprechen die verarbeiteten Daten exakt den Spalten/Feldern in den ICD-CSVs (z.B. `icd_datafetcher.csv`)?
*   [ ] **Typisierung**: Werden Datentypen (Float, Date string, Boolean) korrekt konvertiert?
*   [ ] **Datenfluss**: Passt der Output dieses Moduls zum Input des nächsten Moduls?

### C. Architektur & Logik
*   [ ] **Plausibilität**: Macht der Algorithmus das, was die Anforderung verlangt?
*   [ ] **Edge Cases**: Wurden leere Dateien, Nullen, fehlende Netzwerkverbindung etc. bedacht?
*   [ ] **Redundante Logikpfade**: Gibt es mehrere Code-Pfade, die dasselbe Event/Objekt verarbeiten? (Prüfe auf doppelte if/elif-Bedingungen mit überlappenden Kriterien)
*   [ ] **Performance**: Gibt es offensichtliche Flaschenhälse?

### D. Code-Qualität & Stil
*   [ ] **Lesbarkeit**: Sprechende Variablennamen? Docstrings vorhanden?
*   [ ] **Modularität**: Sind Funktionen fokussiert (Single Responsibility)?
*   [ ] **Hardcoding**: Sind Pfade oder Credentials hartkodiert? (Sollten in Configs/Env-Vars sein).

## 3. Output Format

Erstelle einen Review-Bericht in folgendem Format:

```markdown
# Review Report: [Name des Moduls/Files]

## Zusammenfassung
[Kurzes Fazit: z.B. "Solide Basis, aber kritische Lücke in Fehlerbehandlung"]

## Befunde

### 🔴 Kritisch (Blocker - Muss gefixt werden)
- **[Datei:Zeile]** Problembeschreibung.
    - *Warum:* Erkläre potenzielle Abstürze oder Datenverlust.
    - *Lösungsvorschlag:* Code-Snippet oder Architektur-Hinweis.

### 🟡 Warnungen (Risiken - Sollte gefixt werden)
- **[Datei]** ICD-Abweichung oder Unsauberkeit.

### 🔵 Empfehlungen (Optimierung - Nice to have)
- Code-Stil, Namensgebung, Performance-Tipps.
```
