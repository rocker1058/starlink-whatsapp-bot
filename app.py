import os
from datetime import datetime
import pytz

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# ================= CONFIG =================
SHEET_ID = "1XG6HxVpxMD1HP4sIxCRM4EXCLpVYRReEOHYGqRd0tyM"
SHEET_NAME = "clientes_bot"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly"
]

# ================= HELPERS =================
def esta_al_dia(valor):
    if valor is None:
        return False
    return str(valor).strip().lower() in ["si", "true", "1", "yes"]

def numero_seguro(valor):
    try:
        return int(str(valor).strip())
    except:
        return 0

def formato_pesos(valor):
    return f"{valor:,}".replace(",", ".") + " pesos"

def dividir_mensaje(texto, max_chars=1500):
    if len(texto) <= max_chars:
        return [texto]
    
    lineas = texto.split('\n')
    mensajes = []
    actual = ""
    
    for linea in lineas:
        if len(actual + linea + '\n') > max_chars:
            if actual:
                mensajes.append(actual.strip())
                actual = linea + '\n'
            else:
                mensajes.append(linea)
        else:
            actual += linea + '\n'
    
    if actual:
        mensajes.append(actual.strip())
    
    return mensajes

# ================= ROUTE =================
@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    body = request.values.get("Body", "").lower().strip()
    resp = MessagingResponse()

    # ---- PING (para saber que el bot vive) ----
    if body == "ping":
        resp.message("🏓 Bot activo")
        return str(resp)

    try:
        # ---- AUTH ----
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

        # ---- BLINDAJE TOTAL ----
        if not rows or len(rows) < 2:
            resp.message("⚠️ La hoja no tiene datos válidos.")
            return str(resp)

        headers = rows[0]
        data = rows[1:]
        registros = [dict(zip(headers, row)) for row in data]

        # ---- FECHA COLOMBIA ----
        tz = pytz.timezone("America/Bogota")
        hoy = datetime.now(tz).day

        # ================= PAGOS HOY =================
        if body == "pagos hoy":
            pagos = [
                r for r in registros
                if r.get("cliente")
                and r.get("vence")
                and numero_seguro(r.get("vence")) == hoy
                and not esta_al_dia(r.get("aldia"))
            ]

            if not pagos:
                resp.message("✅ Hoy no hay pagos pendientes.")
                return str(resp)

            total_clientes = 0
            total_starlink = 0

            mensaje = "📅 *Pagos de hoy:*\n\n"

            for r in pagos:
                cliente_paga = numero_seguro(r.get("clientepaga")) * 1000
                tu_pagas = numero_seguro(r.get("paga")) * 1000
                ganancia = cliente_paga - tu_pagas

                total_clientes += cliente_paga
                total_starlink += tu_pagas

                mensaje += (
                    f"- {r.get('cliente')}\n"
                    f"  Cliente paga: {formato_pesos(cliente_paga)}\n"
                    f"  Tú pagas: {formato_pesos(tu_pagas)}\n"
                    f"  Ganancia: {formato_pesos(ganancia)}\n\n"
                )

            mensaje += "————————————\n"
            mensaje += f"💰 Total clientes: {formato_pesos(total_clientes)}\n"
            mensaje += f"📡 Total Starlink: {formato_pesos(total_starlink)}\n"
            mensaje += f"📈 Ganancia total: {formato_pesos(total_clientes - total_starlink)}"

            resp.message(mensaje)
            return str(resp)

        # ================= QUIEN DEBE =================
        if body == "quien debe":
            deudores = [
                r for r in registros
                if r.get("cliente")
                and not esta_al_dia(r.get("aldia"))
            ]

            if not deudores:
                resp.message("🎉 Todos los clientes están al día.")
                return str(resp)

            mensaje = "❌ *Clientes con pago pendiente:*\n\n"

            for r in deudores:
                cliente_paga = numero_seguro(r.get("clientepaga")) * 1000
                tu_pagas = numero_seguro(r.get("paga")) * 1000
                ganancia = cliente_paga - tu_pagas

                mensaje += (
                    f"- {r.get('cliente')}\n"
                    f"  Cliente paga: {formato_pesos(cliente_paga)}\n"
                    f"  Tú pagas: {formato_pesos(tu_pagas)}\n"
                    f"  Ganancia: {formato_pesos(ganancia)}\n\n"
                )

            # Dividir mensaje si es muy largo
            mensajes = dividir_mensaje(mensaje)
            for i, msg in enumerate(mensajes):
                if len(mensajes) > 1:
                    header = f"({i+1}/{len(mensajes)})\n"
                    resp.message(header + msg)
                else:
                    resp.message(msg)
            
            return str(resp)

        # ================= AYUDA =================
        resp.message(
            "🤖 *Comandos disponibles:*\n"
            "- ping\n"
            "- pagos hoy\n"
            "- quien debe"
        )

    except Exception as e:
        resp.message(f"❌ Error interno:\n{repr(e)}")

    return str(resp)

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
