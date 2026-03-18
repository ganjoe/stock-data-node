#!/usr/bin/env python3
"""
Temporäres Script zum Entfernen der minervini_percent Spalte aus allen feature.parquet Dateien.

Dieses Script:
1. Scannt alle Ticker-Verzeichnisse in /data/parquet/
2. Lädt jede <ticker>/1D_features.parquet Datei
3. Entfernt die 'minervini_percent' Spalte falls vorhanden
4. Speichert die bereinigte Datei zurück
"""

from pathlib import Path
import pandas as pd
import sys

# Konfiguration
PARQUET_DIR = Path("/home/daniel/Dokumente/makemoney/stock-data-node/data/parquet")


def remove_minervini_percent_from_file(file_path: Path) -> bool:
    """Entfernt minervini_percent Spalte aus einer einzelnen Datei."""
    try:
        df = pd.read_parquet(file_path)
        
        if 'minervini_percent' in df.columns:
            original_cols = len(df.columns)
            df = df.drop(columns=['minervini_percent'])
            new_cols = len(df.columns)
            
            # Speichern zurück
            df.to_parquet(file_path, index=False)
            return True
        else:
            return False
            
    except Exception as e:
        print(f"  ERROR {file_path}: {e}")
        return None


def main():
    """Hauptfunktion - verarbeitet alle feature.parquet Dateien."""
    
    print("=" * 70)
    print("MINERVINI_PERCENT SPALTE ENTFERNEN")
    print("=" * 70)
    print(f"\nScan-Verzeichnis: {PARQUET_DIR}")
    
    # Finde alle *_features.parquet Dateien
    feature_files = list(PARQUET_DIR.glob("*/*_features.parquet"))
    print(f"Gefundene feature.parquet Dateien: {len(feature_files)}\n")
    
    success_count = 0
    not_found_count = 0
    error_count = 0
    
    for file_path in feature_files:
        result = remove_minervini_percent_from_file(file_path)
        
        if result is True:
            ticker = file_path.parent.name
            print(f"✓ Entfernt aus {ticker}/1D_features.parquet")
            success_count += 1
        elif result is False:
            not_found_count += 1
        else:
            error_count += 1
    
    # Zusammenfassung
    print("\n" + "=" * 70)
    print("ZUSAMMENFASSUNG")
    print("=" * 70)
    print(f"✓ Erfolgreich entfernt: {success_count}")
    print(f"  (Spalte nicht vorhanden): {not_found_count}")
    print(f"✗ Fehler: {error_count}")
    print(f"\nTotal bearbeitet: {len(feature_files)}")


if __name__ == "__main__":
    main()
