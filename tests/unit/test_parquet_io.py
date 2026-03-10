import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from pathlib import Path
from src.features.parquet_io import ParquetStorage

@pytest.fixture
def storage(tmp_path):
    return ParquetStorage(str(tmp_path))

def test_load_ticker_data_exists(storage):
    # Setup - mock read_parquet
    test_df = pd.DataFrame({'a': [1]})
    
    with patch("pandas.read_parquet", return_value=test_df):
        with patch("pathlib.Path.exists", return_value=True):
            df = storage.load_ticker_data("AAPL", "1D")
            assert df.equals(test_df)

def test_load_ticker_data_missing(storage):
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            storage.load_ticker_data("MISSING", "1D")

def test_save_ticker_features(storage, tmp_path):
    df = pd.DataFrame({'f': [1.0]})
    # We don't really need to mock to_parquet if we use tmp_path and check files
    # but mock is faster and cleaner for isolation.
    
    ticker_dir = Path(storage.base_dir) / "AAPL"
    
    with patch("pandas.DataFrame.to_parquet") as mock_to:
        storage.save_ticker_features("AAPL", "1D", df)
        
        # Check that directory was created (via real filesystem since we used tmp_path or mock it too)
        assert ticker_dir.exists()
        mock_to.assert_called_once()
        
def test_get_available_tickers(storage, tmp_path):
    # Create some dummy directories
    (tmp_path / "AAPL").mkdir()
    (tmp_path / "TSLA").mkdir()
    (tmp_path / "other.txt").touch() # Should be ignored (is file)
    
    tickers = storage.get_available_tickers()
    assert "AAPL" in tickers
    assert "TSLA" in tickers
    assert "other.txt" not in tickers
    assert len(tickers) == 2
