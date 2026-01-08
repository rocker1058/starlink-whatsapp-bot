import os
from flask import Flask
from twilio.twiml.messaging_response import MessagingResponse

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

SHEET_ID = "1tPN4C4AeKWZzzG7yx-CFwEjJ2a9FQ6ATj4EVySRlKq8"
SHEET_NAME = "clientes_bot"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly"
]

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    resp = MessagingResponse()

    try:
        credentials = service_account.Credentials.from_service_account_file(
            "/etc/secrets/credentials.json",
            scopes=SCOPES
        )

        service = build("sheets", "v4", credentials=credentials)

        sheet = service.spreadsheets()

        result = sheet.values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A1:Z"
        ).execute()

        values = result.get("values", [])

        filas = len(values) - 1 if len(values) > 1 else 0

        resp.message(
            f"Conectado a Google Sheets ✅\n"
            f"Filas leídas: {filas}"
        )

    except Exception as e:
        resp.message(
            f"Error al leer Google Sheets ❌\n{repr(e)}"
        )

    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
