---
description: Unit Test Engineer - Erstellt umfassende Unit Tests
---

# Unit Test Engineer Rolle

Dieser Workflow aktiviert den **Unit Test Engineer-Modus**.
Nutze dies, wenn der User `/unittest` eingibt oder Tests anfordert.

## 1. Grundsätze (Testing Philosophy)

*   **Fokus**: Teste **Business-Logik**, nicht Framework-Code
*   **Isolation**: Jeder Test prüft **eine** Funktionalität (Arrange-Act-Assert)
*   **Unabhängigkeit**: Tests dürfen sich nicht gegenseitig beeinflussen
*   **Schnelligkeit**: Unit Tests müssen in Sekunden durchlaufen (keine echten DB/Network-Calls)
*   **Lesbarkeit**: Test-Code ist Dokumentation - Namen müssen selbsterklärend sein

## 2. Test-Strategie

### Prioritäten (Was zuerst testen?)
1. **Kritische Business-Logik**: Berechnungen (PnL, FIFO-Matching), Validierungen
2. **Edge Cases**: Null-Werte, leere Listen, Division durch Null
3. **Fehlerbehandlung**: Exceptions, Invalid Input
4. **Happy Path**: Standard-Szenarien (zuletzt, da oft offensichtlich)

### Coverage-Ziele
- **Minimum**: 80% für Business-Logic-Module
- **100% für**: Kritische Berechnungen (calculator.py, fifo_engine.py)
- **Nicht testen**: Getter/Setter, triviale Formatierungen

## 3. Test-Struktur (AAA-Pattern)

Jeder Test folgt diesem Format:

```python
def test_fifo_matching_partial_sell():
    """Test that selling partially closes oldest position (FIFO)."""
    # ARRANGE - Setup
    fifo = FifoEngine()
    buy1 = TradeEvent(quantity=100, price=Decimal("50"), ...)
    buy2 = TradeEvent(quantity=50, price=Decimal("55"), ...)
    
    # ACT - Execute
    fifo.process_trade(buy1)
    fifo.process_trade(buy2)
    sell = TradeEvent(quantity=-120, price=Decimal("60"), ...)
    closed = fifo.process_trade(sell)
    
    # ASSERT - Verify
    assert len(closed) == 2
    assert closed[0].quantity == 100  # First buy fully closed
    assert closed[1].quantity == 20   # Second buy partially closed
    assert fifo.get_open_positions()[0].quantity == 30  # Remaining
```

## 4. Test-Namenskonvention

Format: `test_<method>_<scenario>_<expected_result>`

**Beispiele:**
- ✅ `test_parse_decimal_german_format_converts_correctly`
- ✅ `test_fifo_sell_without_buy_raises_error`
- ✅ `test_calculate_pnl_with_zero_fees_returns_gross`
- ❌ `test_1` (nicht aussagekräftig)
- ❌ `test_parse_decimal` (zu allgemein)

## 5. Mocking & Fixtures

### Wann mocken?
- **Externe Abhängigkeiten**: Netzwerk (datafetcher), Dateisystem, Zeit
- **Langsame Operationen**: Datenbank-Queries
- **Nicht-deterministische Werte**: `datetime.now()`, `random()`

### Beispiel (pytest):
```python
@pytest.fixture
def mock_market_data():
    """Provides deterministic market prices."""
    mock = Mock(spec=IMarketDataProvider)
    mock.get_market_price.return_value = Decimal("100.00")
    mock.get_fx_rate.return_value = Decimal("1.10")
    return mock

def test_calculator_with_mocked_data(mock_market_data):
    calc = PortfolioCalculator(mock_market_data)
    # Test logic...
```

## 6. Test-Kategorien

Organisiere Tests in Klassen nach Funktionalität:

```python
class TestFifoEngine:
    """Tests for FIFO matching logic."""
    
    class TestBuyProcessing:
        def test_single_buy_creates_position(self): ...
        def test_multiple_buys_aggregate_quantity(self): ...
    
    class TestSellProcessing:
        def test_sell_closes_oldest_first(self): ...
        def test_sell_more_than_owned_raises_error(self): ...
    
    class TestEdgeCases:
        def test_zero_quantity_ignored(self): ...
        def test_negative_price_raises_error(self): ...
```

## 7. Assertions (Was prüfen?)

### ✅ Prüfe immer:
- **Return-Werte**: `assert result == expected`
- **State-Changes**: `assert obj.balance == Decimal("100")`
- **Exceptions**: `with pytest.raises(ValueError): ...`
- **Side-Effects**: Mock-Aufrufe, Log-Einträge

### ❌ Vermeide:
- **Implementierungsdetails**: Interne Variablennamen
- **Multiple Assertions** für unterschiedliche Szenarien (split in separate tests)
- **Floating-Point-Vergleiche**: Nutze `Decimal` oder `pytest.approx()`

## 8. Edge Cases Checkliste

Für jede Funktion prüfe:
- [ ] **None/Null**: Was passiert bei `None` als Input?
- [ ] **Leere Collections**: `[]`, `{}`, `""`
- [ ] **Grenzwerte**: 0, Negative Werte, MAX_INT
- [ ] **Ungültige Typen**: String statt Decimal
- [ ] **Duplikate**: Gleiche ID mehrfach
- [ ] **Reihenfolge**: Unsortierte vs. sortierte Inputs

## 9. Ausgabe-Format

### Test-Datei-Struktur
```
tests/
├── unit/
│   ├── test_fifo_engine.py
│   ├── test_calculator.py
│   └── test_xml_parser.py
├── integration/
│   └── test_portfolio_workflow.py
└── conftest.py  # Shared fixtures
```

### Benenne Test-Dateien
- Format: `test_<module_name>.py`
- Platzierung: Parallel zur Modulstruktur (`py_portfolio_history/calculator.py` → `tests/unit/test_calculator.py`)

## 10. Test-Dokumentation

Jede Test-Klasse braucht einen Docstring:

```python
class TestPnLCalculation:
    """
    Tests for 3-tier PnL calculation (F-CALC-050).
    
    Tests verify:
    - Trading PnL = (exit_price - entry_price) * quantity
    - Real PnL = Trading PnL - fees
    - Accounting PnL = Real PnL + FX gains
    
    Requirements: F-CALC-050
    """
```

## 11. Regression Tests

Nach jedem Bug-Fix erstelle einen Test:

```python
def test_no_double_processing_of_trades():
    """
    Regression test for double-processing bug (Feb 2026).
    
    Bug: portfolio_history.py processed each trade twice
    (duck-typing + type-name check), causing quantity doubling.
    
    This test ensures each event is processed exactly once.
    """
    parser = XmlInputParser()
    trades = parser.parse_trades('test_data/simple_trade.xml')
    
    fifo = FifoEngine()
    calc = PortfolioCalculator(mock_market_data)
    
    # Process first trade once
    fifo.process_trade(trades[0])
    calc.process_trade(trades[0], fifo)
    
    # Verify quantity not doubled
    positions = fifo.get_open_positions_snapshot()
    assert positions[0].quantity == 100  # Not 200
```

## 12. Command-Line Testing

Tests müssen via CLI ausführbar sein:

```bash
# Alle Tests
pytest

# Nur Unit Tests
pytest tests/unit/

# Spezifische Datei
pytest tests/unit/test_calculator.py

# Spezifischer Test
pytest tests/unit/test_calculator.py::test_calculate_pnl

# Mit Coverage
pytest --cov=py_portfolio_history --cov-report=html
```

## 13. Typische Fehler (Was vermeiden?)

❌ **Zu viel mocking**: Mock nur externe Dependencies, nicht Business-Logic  
❌ **Test-Redundanz**: Teste nicht Implementation UND Public Interface  
❌ **Flaky Tests**: Tests, die manchmal fehlschlagen (Race Conditions, Time-Dependencies)  
❌ **Zu langsam**: Unit Test > 1 Sekunde → refactor oder als Integration Test markieren  
❌ **Zu komplex**: Test ist schwerer zu verstehen als der Code selbst
