import os
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/etc/secrets/credentials.json"

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import gspread

app = Flask(__name__)

SHEET_ID = "1tPN4C4AeKWZzzG7yx-CFwEjJ2a9FQ6ATj4EVySRlKq8"
SHEET_NAME = "clientes_bot"

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    resp = MessagingResponse()

    try:
        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly"
        ]

        gc = gspread.service_account(
            filename="/etc/secrets/credentials.json",
            scopes=SCOPES
        )
        sh = gc.open_by_key(SHEET_ID)
        worksheet = sh.worksheet(SHEET_NAME)

        filas = worksheet.get_all_records()

        resp.message(
            f"Conectado a Google Sheets ✅\n"
            f"Filas leídas: {len(filas)}"
        )

    except Exception as e:
        resp.message(
            f"Error al leer Google Sheets ❌\n{repr(e)}"
        )

    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
