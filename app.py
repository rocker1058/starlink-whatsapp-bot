import os
from datetime import datetime
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

SHEET_ID = "1XG6HxVpxMD1HP4sIxCRM4EXCLpVYRReEOHYGqRd0tyM"
SHEET_NAME = "clientes_bot"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly"
]

# ---- FUNCION PARA CHECKBOX (SI / NO / TRUE / FALSE) ----
def esta_al_dia(valor):
    if valor is None:
        return False
    v = str(valor).strip().lower()
    return v in ["si", "true", "1", "yes"]

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    body = request.values.get("Body", "").lower().strip()
    resp = MessagingResponse()

    try:
        # ---- AUTENTICACION ----
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

        rows = result.get("values", [])
        headers = rows[0]
        data = rows[1:]

        registros = [dict(zip(headers, row)) for row in data]
        hoy = datetime.now().day

        # ================= PAGOS HOY =================
        if body == "pagos hoy":
            pagos = [
                r for r in registros
                if r.get("vence")
                and int(r["vence"]) == hoy
                and not esta_al_dia(r.get("aldia"))
            ]

            if not pagos:
                resp.message("✅ Hoy no hay pagos pendientes.")
                return str(resp)

            total = 0
            mensaje = "📅 *Pagos de hoy:*\n"

            for r in pagos:
                valor = int(r.get("clientepaga", 0))
                total += valor
                mensaje += f"- {r.get('cliente')} → ${valor}\n"

            mensaje += f"\n💰 *Total esperado:* ${total}"
            resp.message(mensaje)
            return str(resp)

        # ================= QUIEN DEBE =================
        if body == "quien debe":
            deudores = [
                r for r in registros
                if not esta_al_dia(r.get("aldia"))
            ]

            if not deudores:
                resp.message("🎉 Todos los clientes están al día.")
                return str(resp)

            mensaje = "❌ *Clientes con pago pendiente:*\n"
            for r in deudores:
                mensaje += (
                    f"- {r.get('cliente')} "
                    f"(vence día {r.get('vence')}, "
                    f"${r.get('clientepaga')})\n"
                )

            resp.message(mensaje)
            return str(resp)

        # ================= AYUDA =================
        resp.message(
            "🤖 *Comandos disponibles:*\n"
            "- pagos hoy\n"
            "- quien debe"
        )

    except Exception as e:
        resp.message(f"Error ❌\n{repr(e)}")

    return str(resp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
