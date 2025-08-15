import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
import gspread
from google.oauth2.service_account import Credentials

# Připojení ke Google Sheets
def authorize_gsheet():
    GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

    # uložíme obsah proměnné jako originální soubor
    creds_path = "/tmp/google_credentials.json"
    with open(creds_path, "w", encoding="utf-8") as f:
        f.write(GOOGLE_CREDENTIALS_JSON)

    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_file(
        creds_path,
        scopes=scopes
    )

    gc = gspread.authorize(credentials)
    return gc.open_by_key(GOOGLE_SHEET_ID).sheet1

# Odeslání upozornění
def send_email_mailgun(subject, text):
    MAILGUN_API_KEY = os.environ.get("MAILGUN_API_KEY")
    MAILGUN_DOMAIN = os.environ.get("MAILGUN_DOMAIN")
    TO_ADDRESS = os.environ.get("TO_ADDRESS")

    response = requests.post(
        f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}/messages",
        auth=("api", MAILGUN_API_KEY),
        data={
            "from": f"LIDL Watchdog <mailgun@{MAILGUN_DOMAIN}>",
            "to": [TO_ADDRESS],
            "subject": subject,
            "text": text,
        },
    )

    if response.status_code != 200:
        print("Chyba při odesílání e-mailu:", response.text)

# Získání ceny z LIDL API
def get_price_from_api(api_url):
    try:
        response = requests.get(api_url)
        data = response.json()
        return data["price"]["formatted"]
    except:
        return None

# Získání API odkazu z HTML stránky
def extract_api_from_html(product_url):
    try:
        html = requests.get(product_url).text
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script"):
            if tag.string and "product" in tag.string:
                start = tag.string.find("https://www.lidl.cz/")  # API nebo strukturovaná data
                end = tag.string.find(".json") + 5
                if start != -1 and end != -1:
                    return tag.string[start:end]
    except:
        return None

# Hlavní logika
def main():
    sheet = authorize_gsheet()
    rows = sheet.get_all_values()[1:]  # vynecháme hlavičku

    for i, row in enumerate(rows, start=2):
        name = row[0]
        product_url = row[1].strip()
        api_url = row[2].strip()
        interval_days = int(row[3]) if row[3] else 7
        last_checked = datetime.strptime(row[4], "%Y-%m-%d") if row[4] else datetime(2000, 1, 1)
        stored_price = row[5].replace(" Kč", "").replace(",", ".") if row[5] else ""
        status = row[6] if len(row) > 6 else ""

        # 0) Kontrola intervalu
        if (datetime.now() - last_checked).days < interval_days:
            continue

        price = None
        available = True  # předpokládáme dostupnost

        # 1) Pokud existuje API URL ve sloupci C, zkus ho použít
        if api_url:
            price = get_price_from_api(api_url)

        # 2) Pokud API nevrátilo cenu, zkus z HTML získat API URL a znovu použít
        if not price:
            extracted_api = extract_api_from_html(product_url)
            if extracted_api and extracted_api != api_url:
                sheet.update_cell(i, 3, extracted_api)
                api_url = extracted_api
                price = get_price_from_api(api_url)

        # 3) Pokud ani teď cena není, zkus přímo vyčíst z HTML stránky
        if not price:
            try:
                html = requests.get(product_url).text
                soup = BeautifulSoup(html, "html.parser")
                tag = soup.find("span", {"class": "m-price__price"})
                if tag:
                    price = tag.get_text(strip=True).replace('\xa0', ' ').replace("Kč", "").strip()
                else:
                    available = False
            except:
                available = False

        # 4) Pokud není dostupné
        if not available or not price:
            send_email_mailgun(f"{name} je nedostupné", f"Odkaz: {product_url}")
            sheet.update_cell(i, 7, "Nedostupné")
            sheet.update_cell(i, 5, datetime.now().strftime("%Y-%m-%d"))
            continue
        else:
            sheet.update_cell(i, 7, "")  # vyčistíme status "Nedostupné"

        # 5) Pokud nemáme uloženou cenu, uložíme aktuální
        if not stored_price:
            sheet.update_cell(i, 6, f"{price} Kč")
            sheet.update_cell(i, 5, datetime.now().strftime("%Y-%m-%d"))
            continue

        # 6) Porovnání cen
        try:
            price_float = float(price.replace(",", "."))
            stored_float = float(stored_price)
        except ValueError:
            sheet.update_cell(i, 5, datetime.now().strftime("%Y-%m-%d"))
            continue

        if price_float < stored_float:
            send_email_mailgun(
                f"{name} je ve slevě!",
                f"Nová cena: {price} Kč\nOdkaz: {product_url}"
            )
            sheet.update_cell(i, 6, f"{price} Kč")

        elif price_float > stored_float:
            sheet.update_cell(i, 6, f"{price} Kč")

        # 7) Na konci zapíšeme datum poslední kontroly
        sheet.update_cell(i, 5, datetime.now().strftime("%Y-%m-%d"))

if __name__ == "__main__":
    main()
