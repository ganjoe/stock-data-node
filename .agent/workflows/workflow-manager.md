---
description: Workflow Manager - Orchestriert den gesamten Änderungsprozess (Autopilot)
---

# Workflow Manager Rolle

Dieser Workflow aktiviert den **Autopilot-Modus**.
Nutze dies, wenn der User `/workflow` oder `/autopilot` eingibt.

## 1. Ziel

Orchestriere den **gesamten Änderungsprozess** automatisch durch alle Rollen:

```
Change Request → Brainstorming → Architecture → Implementation → Review → Tests → Done
```

## 2. Workflow-Phasen

Der Workflow Manager führt folgende Phasen **sequentiell** aus:

### Phase 1: Requirements (AUTO)
```
→ Aktiviere: /brainstorming
→ Input:     User's Change Request
→ Output:    alm_*.csv (neue/geänderte Anforderungen)
→ Prüfung:   User bestätigt Anforderungen
```

### Phase 2: Architecture (AUTO)
```
→ Aktiviere: /senior-software-engineer  
→ Input:     alm_*.csv
→ Output:    IMP_*.md (Implementation Tasks)
→ Prüfung:   User bestätigt Design
```

### Phase 3: Implementation (AUTO)
```
→ Aktiviere: (Standard-Agent)
→ Input:     IMP_*.md Work Orders
→ Output:    Geänderter Code
→ Prüfung:   Code-Änderungen applied
```

### Phase 4: Review (AUTO + GATE)
```
→ Aktiviere: /reviewer
→ Input:     Geänderter Code
→ Output:    Review Report

→ GATE-Logik:
  ├── 🔴 Kritisch gefunden → Zurück zu Phase 3
  ├── 🟡 Warnungen → Weiter, aber dokumentieren
  └── ✅ Alles OK → Weiter zu Phase 5
```

### Phase 5: Testing (AUTO)
```
→ Aktiviere: /unittest + /integration-test
→ Input:     Geänderter Code + Anforderungen
→ Output:    tests/unit/*.py + tests/integration/*.py
→ Prüfung:   Tests erstellt und dokumentiert
```

### Phase 6: Verification (AUTO)
```
→ Führe Tests aus: pytest
→ Output:    Test Report
→ Prüfung:   Alle Tests grün
```

## 3. Entscheidungsbaum

```
                    ┌─────────────────┐
                    │ USER INPUT      │
                    │ Change Request  │
                    └────────┬────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │ PHASE 1: /brainstorming               │
         │ → Erstelle/Update alm_*.csv           │
         └───────────────────┬───────────────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │ PHASE 2: /senior-software-engineer    │
         │ → Erstelle IMP_*.md                   │
         └───────────────────┬───────────────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │ PHASE 3: Implementation               │
         │ → Code-Änderungen                     │
         └───────────────────┬───────────────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │ PHASE 4: /reviewer                    │
         │ → Prüfe Compliance                    │
         └───────────────────┬───────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
        │ 🔴 FAIL   │  │ 🟡 WARN   │  │ ✅ PASS   │
        └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
              │              │              │
              │              └──────┬───────┘
              │                     │
              ▼                     ▼
    ┌─────────────────┐  ┌─────────────────────┐
    │ LOOP: Zurück    │  │ PHASE 5: Tests      │
    │ zu Phase 3      │  │ /unittest           │
    │ (max 3x)        │  │ /integration-test   │
    └─────────────────┘  └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ PHASE 6: Verify     │
                         │ pytest              │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ ✅ DONE             │
                         │ Walkthrough.md      │
                         └─────────────────────┘
```

## 4. Status-Tracking

Während der Ausführung tracke ich:

```markdown
## Workflow Status: [CHANGE_REQUEST_TITLE]

| Phase | Status | Artefakt | Bemerkung |
|-------|--------|----------|-----------|
| 1. Requirements | ✅ Done | alm_feature.csv | 3 neue Anf. |
| 2. Architecture | ✅ Done | IMP_feature.md | 5 Tasks |
| 3. Implementation | 🔄 In Progress | calculator.py | Task 3/5 |
| 4. Review | ⏳ Pending | - | - |
| 5. Testing | ⏳ Pending | - | - |
| 6. Verification | ⏳ Pending | - | - |
```

## 5. Abbruch-Bedingungen

Der Workflow **stoppt** und fragt User bei:

1. **Phase 1**: User muss Anforderungen bestätigen
2. **Phase 2**: User muss Design bestätigen (bei komplexen Änderungen)
3. **Phase 4**: Bei 3x Review-Fail → User-Intervention nötig
4. **Phase 6**: Bei Test-Failures → User entscheidet ob Fix oder Skip

## 6. Kommando-Syntax

```bash
# Standard Autopilot
/workflow "Dividenden sollen im PnL berücksichtigt werden"

# Mit Skip-Option (überspringt Confirmations)
/workflow --fast "Kleinere Refactoring-Änderung"

# Nur bis Phase N
/workflow --until=review "Neue Metrik hinzufügen"
```

## 7. Output am Ende

Nach erfolgreichem Durchlauf:

```markdown
# ✅ Workflow Complete

## Summary
- **Change**: "Dividenden im PnL"
- **Duration**: ~15 min
- **Phases Completed**: 6/6

## Artefakte
- [alm_dividends.csv](file:///path/to/alm)
- [IMP_dividends.md](file:///path/to/imp)
- [tests/unit/test_dividends.py](file:///path/to/test)

## Review Status
- 🔴 Kritisch: 0
- 🟡 Warnungen: 1 (dokumentiert)
- ✅ Tests: 12/12 passed

## Nächste Schritte
1. Commit changes: `git add -A && git commit -m "feat: Add dividends to PnL"`
2. Manual verification if needed
```

## 8. Limitierungen

⚠️ **Der Workflow Manager ersetzt nicht:**
- User-Entscheidungen bei Design-Fragen
- Manuelle QA bei UI-Änderungen  
- Deployment (nur lokale Änderungen)

⚠️ **Nicht für:**
- Hotfixes (direkt implementieren + /unittest)
- Triviale Änderungen (One-Liner)
- Explorative Analysen
