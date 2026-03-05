import unittest
import sys
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from downloader import classify_error
from models import ErrorCategory

class TestClassification(unittest.TestCase):
    def test_classify_error(self):
        self.assertEqual(classify_error(" Historical Market Data Service error message:No market data permissions for NYMEX STK"), ErrorCategory.NO_PERMISSIONS)
        self.assertEqual(classify_error("Error 200, reqId 4: No security definition has been found for the request"), ErrorCategory.QUALIFY_FAILED)
        self.assertEqual(classify_error("HMDS query returned no data: AAPL 15 mins"), ErrorCategory.NO_DATA)
        self.assertEqual(classify_error("Historical data request timeout"), ErrorCategory.TIMEOUT)
        self.assertEqual(classify_error("Max number of pacing violations"), ErrorCategory.PACING)
        self.assertEqual(classify_error("Historical data request cancelled"), ErrorCategory.CANCELLED)
        self.assertEqual(classify_error("Some random unknown error"), ErrorCategory.UNKNOWN)
        self.assertEqual(classify_error(""), ErrorCategory.UNKNOWN)

if __name__ == '__main__':
    unittest.main()
