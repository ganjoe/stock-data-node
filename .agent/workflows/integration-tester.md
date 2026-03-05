---
description: Integration Test Engineer - Testet Zusammenspiel mehrerer Komponenten
---

# Integration Test Engineer Rolle

Dieser Workflow aktiviert den **Integration Test Engineer-Modus**.
Nutze dies, wenn der User `/integration-test` eingibt oder End-to-End Tests anfordert.

## 1. Grundsätze (Integration Testing Philosophy)

*   **Fokus**: Teste das **Zusammenspiel** von Komponenten, nicht Einzelfunktionen
*   **Realität**: Nutze echte Abhängigkeiten wo möglich (echte Dateien, DB, aber keine externen APIs)
*   **Szenarien**: Teste komplette User-Workflows vom Input bis Output
*   **Datenkonsistenz**: Prüfe ICD-Konformität zwischen Modulen
*   **Geschwindigkeit**: < 30 Sekunden pro Test (langsamer als Unit Tests, aber nicht Minuten)

## 2. Was ist ein Integration Test?

### Integration Test testet:
✅ Parser → Domain Models → Calculator → XML Generator (gesamter Workflow)
✅ Datei-Input → Verarbeitung → Datei-Output
✅ Modul A ruft Modul B auf und übergibt korrekte Datenstruktur
✅ ICD-Compliance zwischen Schnittstellen

### Kein Integration Test (das ist Unit Test):
❌ Einzelne Funktion in calculator.py
❌ FIFO-Engine ohne echte Trades
❌ Mocked dependencies ohne echte Interaktion

## 3. Test-Strategie

### Testpyramide
```
        /\
       /E2E\     ← Wenige (< 10), nur kritische Happy Paths
      /----\
     / Integ \   ← Moderate Anzahl (20-50), wichtige Workflows
    /--------\
   /   Unit   \  ← Viele (100+), alle Edge Cases
  /____________\
```

### Prioritäten
1. **Critical Path**: Hauptworkflow (trades.xml → portfolio-history.xml)
2. **Error Handling**: Ungültige XML, fehlende Daten
3. **ICD Compliance**: Output entspricht exakt icd_portfolio-history.csv
4. **Edge Cases**: Leere Trades, nur Deposits, nur Dividenden

## 4. Test-Struktur

```python
class TestPortfolioHistoryWorkflow:
    """Integration tests for complete portfolio history workflow."""
    
    def test_full_workflow_with_real_data(self, tmp_path):
        """
        End-to-End test: Parse trades.xml → Calculate → Generate XML.
        
        Verifies:
        - XML parsing works with real data
        - FIFO matching produces correct closed trades
        - Calculator computes correct metrics
        - XML output is valid and ICD-compliant
        """
        # ARRANGE - Setup test data
        input_file = tmp_path / "test_trades.xml"
        output_file = tmp_path / "output.xml"
        
        # Create minimal valid trades.xml
        input_file.write_text("""
<TradeLog>
  <Trades>
    <Trade id="t1" isin="TEST001">
      <Meta><Date>01.01.2026</Date><Time>10:00:00</Time></Meta>
      <Instrument><Symbol>STKA</Symbol><Currency>EUR</Currency></Instrument>
      <Execution><Quantity>100</Quantity><Price>50,00</Price>
        <Commission>-10,00</Commission><Proceeds>-5010,00</Proceeds>
      </Execution>
    </Trade>
  </Trades>
  <DepositsWithdrawals></DepositsWithdrawals>
  <Dividends></Dividends>
</TradeLog>
        """)
        
        # ACT - Run complete workflow
        from py_portfolio_history.portfolio_history import main
        sys.argv = ['test', '--input', str(input_file), '--output', str(output_file)]
        main()
        
        # ASSERT - Verify output
        assert output_file.exists()
        
        # Parse generated XML
        tree = ET.parse(output_file)
        root = tree.getroot()
        
        # Verify structure
        assert root.tag == "PortfolioHistory"
        assert root.find("Performance") is not None
        
        # Verify metrics
        trades_elem = root.find("Performance/TotalTrades")
        assert trades_elem.find("Transactions").text == "1"
```

## 5. Test-Daten Management

### Strategie: Fixture-Dateien
```
tests/
├── fixtures/
│   ├── trades_minimal.xml       # 1 trade
│   ├── trades_fifo_complex.xml  # Multiple buys/sells
│   ├── trades_invalid.xml       # Broken XML
│   └── expected_output_minimal.xml
```

### Verwendung
```python
@pytest.fixture
def minimal_trades_file():
    """Returns path to minimal valid trades.xml."""
    return Path(__file__).parent / "fixtures" / "trades_minimal.xml"

def test_parser_with_fixture(minimal_trades_file):
    parser = XmlInputParser()
    trades = parser.parse_trades(str(minimal_trades_file))
    assert len(trades) == 1
```

## 6. ICD Compliance Tests

**Kritisch**: Output XML muss **exakt** dem ICD entsprechen!

```python
def test_output_xml_matches_icd_structure():
    """Verify output XML structure matches icd_portfolio-history.csv."""
    # Run workflow
    output_xml = generate_portfolio_history("test_trades.xml")
    
    # Parse ICD requirements
    icd = pd.read_csv("icd_portfolio-history.csv")
    required_nodes = icd[icd['Type'] == 'Node']['Name'].tolist()
    
    # Verify all required nodes exist
    tree = ET.fromstring(output_xml)
    for node_name in required_nodes:
        assert tree.find(f".//{node_name}") is not None, \
            f"Missing required node: {node_name}"
    
    # Verify attributes
    perf = tree.find(".//Performance/TotalRealizedPnL")
    assert perf.get("currency") == "EUR", "Missing currency attribute"
```

## 7. Fehler-Szenarien

### Teste Robustheit
```python
def test_parser_handles_missing_optional_fields():
    """Parser should not crash on missing non-required fields."""
    xml = """<Trade id="t1">
        <Meta><Date>01.01.2026</Date></Meta>
        <Execution><Quantity>100</Quantity><Price>50</Price></Execution>
    </Trade>"""
    # Missing: Time, Commission, ISIN
    
    parser = XmlInputParser()
    trades = parser.parse_trades(xml)
    
    assert len(trades) == 1
    assert trades[0].commission == Decimal("0")  # Default

def test_workflow_with_empty_trades_file():
    """System should handle empty trades gracefully."""
    xml = "<TradeLog><Trades></Trades></TradeLog>"
    
    # Should not crash
    result = run_portfolio_history(xml)
    
    assert result.find("Performance/TotalTrades/Transactions").text == "0"
```

## 8. Golden Master Testing

Für stabile Workflows: Snapshot-Testing

```python
def test_output_matches_golden_master(tmp_path):
    """Regression test: Output should match known-good baseline."""
    # Run workflow
    output_file = tmp_path / "output.xml"
    run_portfolio_history("fixtures/trades_stable.xml", output_file)
    
    # Load golden master
    golden = Path("fixtures/expected_output_stable.xml").read_text()
    actual = output_file.read_text()
    
    # Normalize (remove timestamps, whitespace)
    golden_norm = normalize_xml(golden)
    actual_norm = normalize_xml(actual)
    
    assert actual_norm == golden_norm, \
        "Output differs from golden master. Review changes."
```

## 9. Performance Tests

Integration Tests sollten Performanz prüfen:

```python
def test_large_portfolio_performance():
    """Workflow should complete in < 10 seconds for 1000 trades."""
    # Generate large test file
    trades_xml = generate_test_trades(count=1000)
    
    import time
    start = time.time()
    
    run_portfolio_history(trades_xml)
    
    duration = time.time() - start
    assert duration < 10.0, f"Workflow too slow: {duration:.2f}s"
```

## 10. Database/State Tests

Falls Zustand persistent ist:

```python
class TestPortfolioStatePersistence:
    """Tests for state management across runs."""
    
    def setup_method(self):
        """Clean state before each test."""
        clear_cache()
        reset_database()
    
    def test_incremental_updates(self):
        """Running twice should produce same result."""
        run1 = run_portfolio_history("trades.xml")
        run2 = run_portfolio_history("trades.xml")
        
        assert run1 == run2  # Idempotent
```

## 11. Mocking in Integration Tests

**Regel**: Mock nur **externe** Services (Internet, Paid APIs)

```python
@pytest.fixture
def mock_external_api():
    """Mock only external market data API, not internal modules."""
    with patch('py_datafetcher.provider_yahoo.YahooProvider') as mock:
        mock.return_value.fetch_ohlc.return_value = test_data
        yield mock

def test_with_mocked_external_api(mock_external_api):
    """Use real calculator, parser, etc. but mocked Yahoo API."""
    # Real workflow with mocked external dependency
    result = run_portfolio_history("trades.xml")
    
    # Verify API was called
    mock_external_api.fetch_ohlc.assert_called()
```

## 12. Test-Organisation

```
tests/
├── integration/
│   ├── test_portfolio_workflow.py      # Main workflow
│   ├── test_parser_to_calculator.py    # Parser → Calc
│   ├── test_calculator_to_xml.py       # Calc → XML
│   ├── test_error_handling.py          # Error scenarios
│   └── test_icd_compliance.py          # ICD validation
├── fixtures/
│   └── ...
└── conftest.py
```

## 13. Continuous Integration

Tests müssen in CI laufen:

```yaml
# .github/workflows/tests.yml
- name: Run Integration Tests
  run: |
    pytest tests/integration/ \
      --junit-xml=test-results/integration.xml \
      --cov=py_portfolio_history
```

## 14. Typische Fehler (Was vermeiden?)

❌ **Zu granular**: Teste nicht jede einzelne Methode mit Integration Test  
❌ **Echte APIs**: Keine echten Yahoo/Bloomberg Calls (teuer, flaky)  
❌ **Hardcoded Paths**: Nutze `tmp_path` oder relative Pfade  
❌ **Zu langsam**: Integration Test > 30s → refactor zu kleineren Tests  
❌ **Stateful**: Tests beeinflussen sich gegenseitig → cleanup in `teardown`

## 15. Checkliste für Integration Tests

Für jeden Workflow teste:
- [ ] **Happy Path**: Standard-Szenario funktioniert
- [ ] **Empty Input**: Leere Dateien crashen nicht
- [ ] **Invalid Input**: Fehlerhafte XML wird abgefangen
- [ ] **ICD Compliance**: Output entspricht Spezifikation
- [ ] **Data Flow**: Korrekte Übergabe zwischen Modulen
- [ ] **Performance**: Unter akzeptablem Zeitlimit
- [ ] **Idempotenz**: Mehrfaches Ausführen = gleiches Ergebnis
- [ ] **Cleanup**: Temporäre Dateien werden gelöscht
