import gspread
import time
from oauth2client.service_account import ServiceAccountCredentials

# Konfiguracja Google Sheets
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
client = gspread.authorize(creds)
sheet = client.open('Struktura bazy danych - Metadane Generacji AI V2').sheet1

def process_tasks():
    print("Sprawdzam zadania w arkuszu...")
    # Pobieramy wszystkie dane
    data = sheet.get_all_records()
    
    for i, row in enumerate(data):
        # Sprawdzamy, czy wiersz ma status "Do zrobienia"
        if row['status'] == 'Do zrobienia':
            print(f"Znaleziono nowe zadanie: {row['temat']}")
            
            # --- TU WSTAWISZ WYWOŁANIE SWOJEGO API HUGGING FACE ---
            print(f"Generuję wideo dla scenariusza: {row['skrypt']}")
            # Symulacja pracy
            time.sleep(2) 
            # ------------------------------------------------------
            
            # Aktualizacja statusu na "Gotowe" w arkuszu
            # +2, bo wiersze w gspread zaczynają się od 1 + nagłówek
            sheet.update_cell(i + 2, sheet.find('status').col, 'Gotowe')
            print("Zadanie zakończone i zaktualizowane w arkuszu.")

# Pętla działająca w nieskończoność
while True:
    try:
        process_tasks()
    except Exception as e:
        print(f"Wystąpił błąd: {e}")
    
    print("Czekam 60 sekund przed kolejnym sprawdzeniem...")
    time.sleep(60)
