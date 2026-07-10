import gspread
import logging
from oauth2client.service_account import ServiceAccountCredentials
from typing import Optional

logger = logging.getLogger(__name__)

# Cache for sheet connection to avoid repeated auth calls
_sheet_cache: Optional[gspread.Worksheet] = None
_client_cache: Optional[gspread.Client] = None

def connect_to_sheet() -> gspread.Worksheet:
    """
    Authorize connection to Google Sheets and return worksheet.
    Reuses existing connection to reduce API overhead.
    """
    global _sheet_cache, _client_cache
    
    if _sheet_cache is not None:
        logger.debug("✅ Używam cached połączenia z Google Sheets.")
        return _sheet_cache
    
    logger.info("🔌 Łączenie z Google Sheets...")
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        _client_cache = gspread.authorize(creds)
        _sheet_cache = _client_cache.open('Struktura bazy danych - Metadane Generacji AI V2').sheet1
        logger.info("✅ Połączono z arkuszem pomyślnie.")
        return _sheet_cache
    except FileNotFoundError:
        logger.error("❌ Brak pliku credentials.json. Zaloguj się do Google Cloud Console.")
        raise
    except Exception as e:
        logger.error(f"❌ Błąd autoryzacji Google Sheets: {e}")
        raise

def reset_sheet_connection():
    """Force reconnection to Google Sheets (for manual refresh)."""
    global _sheet_cache, _client_cache
    _sheet_cache = None
    _client_cache = None
    logger.info("🔄 Cache połączenia Google Sheets wyczyszczony.")
