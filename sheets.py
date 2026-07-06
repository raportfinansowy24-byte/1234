import gspread
import logging
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

def connect_to_sheet():
    """Autoryzuje połączenie z Google Sheets i zwraca arkusz roboczy."""
    logger.info("🔌 Łączenie z Google Sheets...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    client = gspread.authorize(creds)
    sheet = client.open('Struktura bazy danych - Metadane Generacji AI V2').sheet1
    logger.info("✅ Połączono z arkuszem pomyślnie.")
    return sheet
