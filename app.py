import os
from datetime import datetime, timedelta
import pytz
from docx import Document
import tempfile
import subprocess
import boto3
from botocore.exceptions import ClientError

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# ================= CONFIG =================
SHEET_ID = "1XG6HxVpxMD1HP4sIxCRM4EXCLpVYRReEOHYGqRd0tyM"
SHEET_NAME = "clientes_bot"
S3_BUCKET = os.environ.get("S3_BUCKET", "starlink-facturas-bot-2025")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
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

def obtener_siguiente_numero():
    """Obtiene y actualiza el número consecutivo de factura"""
    contador_path = "contador_facturas.txt"
    if not os.path.exists(contador_path):
        with open(contador_path, 'w') as f:
            f.write('1')
        return 1
    
    with open(contador_path, 'r') as f:
        numero = int(f.read().strip())
    
    # Actualizar contador
    with open(contador_path, 'w') as f:
        f.write(str(numero + 1))
    
    return numero

def generar_factura_cliente(cliente_data):
    """Genera factura para un cliente"""
    try:
        # Datos para la factura
        fecha_actual = datetime.now(pytz.timezone("America/Bogota")).strftime("%d/%m/%Y")
        numero_factura = f"FAC-{obtener_siguiente_numero():03d}"
        monto = numero_seguro(cliente_data.get("clientepaga")) * 1000
        
        # Cargar plantilla
        doc = Document("FACTURA MARAVILLA (4).docx")
        
        # Modificar campos en las tablas
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    # Reemplazar campos
                    if "PA-02-R-02-4" in cell.text:
                        cell.text = cell.text.replace("PA-02-R-02-4", numero_factura)
                    if "19/08/2025" in cell.text:
                        cell.text = cell.text.replace("19/08/2025", fecha_actual)
                    if "MARAVILLA" in cell.text and "OCTAVA MARAVILLA" not in cell.text:
                        cell.text = cell.text.replace("MARAVILLA", cliente_data.get("cliente").upper())
                    if "SERVICIO ANTENA OCTAVA MARAVILLA" in cell.text:
                        meses = {
                            'January': 'ENERO', 'February': 'FEBRERO', 'March': 'MARZO',
                            'April': 'ABRIL', 'May': 'MAYO', 'June': 'JUNIO',
                            'July': 'JULIO', 'August': 'AGOSTO', 'September': 'SEPTIEMBRE',
                            'October': 'OCTUBRE', 'November': 'NOVIEMBRE', 'December': 'DICIEMBRE'
                        }
                        mes_ingles = datetime.now().strftime("%B")
                        mes_espanol = meses.get(mes_ingles, mes_ingles.upper())
                        cell.text = f"SERVICIO ANTENA PERIODO {mes_espanol}"
                    if "$450,000" in cell.text:
                        cell.text = cell.text.replace("$450,000", formato_pesos(monto))

        # Guardar factura Word (completa)
        factura_path = f"factura_{numero_factura}_{cliente_data.get('cliente').replace(' ', '_')}.docx"
        doc.save(factura_path)
        
        # Convertir a PDF con LibreOffice
        try:
            result = subprocess.run([
                "libreoffice", "--headless", "--convert-to", "pdf", factura_path
            ], capture_output=True, check=True, timeout=30)
            
            pdf_path = factura_path.replace('.docx', '.pdf')
            if os.path.exists(pdf_path):
                return pdf_path, numero_factura
            else:
                return factura_path, numero_factura
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            # Si LibreOffice falla, devolver Word
            return factura_path, numero_factura

    except Exception as e:
        return None, str(e)

def subir_a_s3(archivo_path, nombre_archivo):
    """Sube archivo a S3 y retorna URL pública"""
    try:
        s3_client = boto3.client('s3', region_name=S3_REGION)
        
        # Subir archivo
        s3_key = f"facturas/{nombre_archivo}"
        s3_client.upload_file(archivo_path, S3_BUCKET, s3_key)
        
        # Generar URL pública
        url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_key}"
        return url
        
    except Exception as e:
        return None

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

        # ================= PROXIMOS PAGOS =================
        if body == "proximos pagos":
            # Obtener fecha actual y próximos 3 días (incluyendo hoy)
            fecha_actual = datetime.now(tz)
            dias_a_revisar = []
            
            for i in range(4):  # Hoy + 3 días = 4 días
                fecha = fecha_actual + timedelta(days=i)
                dias_a_revisar.append(fecha.day)
            
            proximos = [
                r for r in registros
                if r.get("cliente")
                and r.get("vence")
                and numero_seguro(r.get("vence")) in dias_a_revisar
                and not esta_al_dia(r.get("aldia"))
            ]

            if not proximos:
                resp.message("✅ No hay pagos pendientes en los próximos 3 días.")
                return str(resp)

            mensaje = "⏰ *Pagos próximos (3 días):*\n\n"

            for r in proximos:
                dia_vence = numero_seguro(r.get("vence"))
                cliente_paga = numero_seguro(r.get("clientepaga")) * 1000
                
                mensaje += (
                    f"- {r.get('cliente')}\n"
                    f"  Vence: día {dia_vence}\n"
                    f"  Monto: {formato_pesos(cliente_paga)}\n\n"
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

        # ================= MARCAR PAGO =================
        if " pago" in body and body != "proximos pagos":
            nombre_cliente = body.replace(" pago", "").strip()
            
            # Buscar cliente
            cliente_encontrado = None
            fila_cliente = None
            
            for i, r in enumerate(registros):
                if r.get("cliente") and nombre_cliente.lower() in r.get("cliente").lower():
                    cliente_encontrado = r
                    fila_cliente = i + 2  # +2 porque Excel empieza en 1 y hay header
                    break
            
            if not cliente_encontrado:
                resp.message(f"❌ No encontré al cliente '{nombre_cliente}'")
                return str(resp)
            
            # Actualizar Excel - marcar como pagado
            try:
                # La columna "aldia" es la H (posición 8)
                sheet.values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"{SHEET_NAME}!H{fila_cliente}",
                    valueInputOption="RAW",
                    body={"values": [["SI"]]}
                ).execute()
                
                resp.message(f"✅ {cliente_encontrado.get('cliente')} marcado como pagado")
                
            except Exception as e:
                resp.message(f"❌ Error al actualizar: {str(e)}")
            
            return str(resp)

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

        # ================= GENERAR FACTURA =================
        if body.startswith("factura "):
            nombre_cliente = body.replace("factura ", "").strip()
            
            # Buscar cliente - primero coincidencia exacta, luego parcial
            cliente_encontrado = None
            coincidencias = []
            
            for r in registros:
                if r.get("cliente"):
                    cliente_nombre = r.get("cliente").strip().lower()
                    nombre_buscar = nombre_cliente.strip().lower()
                    # Coincidencia exacta
                    if cliente_nombre == nombre_buscar:
                        cliente_encontrado = r
                        break
                    # Coincidencia parcial
                    elif nombre_buscar in cliente_nombre:
                        coincidencias.append(r)
            
            # Si no hay coincidencia exacta, usar la primera parcial
            if not cliente_encontrado and coincidencias:
                if len(coincidencias) == 1:
                    cliente_encontrado = coincidencias[0]
                else:
                    # Múltiples coincidencias - mostrar opciones
                    mensaje = f"❓ Encontré varios clientes con '{nombre_cliente}':\n\n"
                    for r in coincidencias:
                        mensaje += f"- {r.get('cliente')}\n"
                    mensaje += "\nEscribe el nombre más específico."
                    resp.message(mensaje)
                    return str(resp)
            
            if not cliente_encontrado:
                resp.message(f"❌ No encontré al cliente '{nombre_cliente}'")
                return str(resp)
            
            try:
                archivo_path, numero_factura = generar_factura_cliente(cliente_encontrado)
                if archivo_path:
                    # Subir a S3
                    nombre_archivo = os.path.basename(archivo_path)
                    url_descarga = subir_a_s3(archivo_path, nombre_archivo)
                    
                    if url_descarga:
                        resp.message(f"✅ Factura {numero_factura} generada para {cliente_encontrado.get('cliente')}\n📄 Descargar: {url_descarga}")
                        # Limpiar archivo local
                        try:
                            os.remove(archivo_path)
                        except:
                            pass
                    else:
                        resp.message(f"✅ Factura {numero_factura} generada pero error subiendo a S3\n📄 Archivo local: {archivo_path}")
                else:
                    resp.message(f"❌ Error generando factura")
            except Exception as e:
                resp.message(f"❌ Error generando factura: {str(e)}")
            
            return str(resp)

        # ================= CUANTO DEBE [NOMBRE] =================
        if body.startswith("cuanto debe "):
            nombre_cliente = body.replace("cuanto debe ", "").strip()
            
            # Buscar cliente - primero coincidencia exacta, luego parcial
            cliente_encontrado = None
            coincidencias = []
            
            for r in registros:
                if r.get("cliente"):
                    cliente_nombre = r.get("cliente").strip().lower()
                    nombre_buscar = nombre_cliente.strip().lower()
                    # Coincidencia exacta
                    if cliente_nombre == nombre_buscar:
                        cliente_encontrado = r
                        break
                    # Coincidencia parcial
                    elif nombre_buscar in cliente_nombre:
                        coincidencias.append(r)
            
            # Si no hay coincidencia exacta, usar la primera parcial
            if not cliente_encontrado and coincidencias:
                if len(coincidencias) == 1:
                    cliente_encontrado = coincidencias[0]
                else:
                    # Múltiples coincidencias - mostrar opciones
                    mensaje = f"❓ Encontré varios clientes con '{nombre_cliente}':\n\n"
                    for r in coincidencias:
                        mensaje += f"- {r.get('cliente')}\n"
                    mensaje += "\nEscribe el nombre más específico."
                    resp.message(mensaje)
                    return str(resp)
            
            if not cliente_encontrado:
                resp.message(f"❌ No encontré al cliente '{nombre_cliente}'")
                return str(resp)
            
            # Construir respuesta con toda la información
            al_dia = esta_al_dia(cliente_encontrado.get("aldia"))
            estado = "Al día" if al_dia else "En mora"
            dia_vence = numero_seguro(cliente_encontrado.get("vence"))
            cliente_paga = numero_seguro(cliente_encontrado.get("clientepaga")) * 1000
            tu_pagas = numero_seguro(cliente_encontrado.get("paga")) * 1000
            ganancia = cliente_paga - tu_pagas
            correo = cliente_encontrado.get("correo", "No registrado")
            serial = cliente_encontrado.get("serial_antena", "No registrado")
            kit = cliente_encontrado.get("kit_antena", "")
            nota = cliente_encontrado.get("NOTA", "")
            
            mensaje = (
                f"Información de {cliente_encontrado.get('cliente')}\n\n"
                f"{estado}\n"
                f"Vence: día {dia_vence}\n"
                f"Cliente paga: {formato_pesos(cliente_paga)}\n"
                f"Tú pagas: {formato_pesos(tu_pagas)}\n"
                f"Ganancia: {formato_pesos(ganancia)}\n"
                f"Corte del servicio: día {dia_vence}\n"
                f"Correo: {correo}\n"
                f"Serial antena: {serial}\n"
                f"Kit: {kit}"
            )
            
            if nota:
                mensaje += f"\nNota: {nota}"
            
            resp.message(mensaje)
            return str(resp)

        # ================= BUSQUEDA POR SERIAL =================
        # Detectar si el mensaje parece un serial (contiene "stk" o formato similar)
        if "stk" in body or (len(body) > 5 and "-" in body):
            serial_buscar = body.strip()
            
            # Buscar por serial
            cliente_encontrado = None
            for r in registros:
                if r.get("serial") and serial_buscar.lower() in r.get("serial").lower():
                    cliente_encontrado = r
                    break
            
            if cliente_encontrado:
                # Mostrar la misma información que "cuanto debe"
                al_dia = esta_al_dia(cliente_encontrado.get("aldia"))
                estado = "Al día" if al_dia else "En mora"
                dia_vence = numero_seguro(cliente_encontrado.get("vence"))
                cliente_paga = numero_seguro(cliente_encontrado.get("clientepaga")) * 1000
                tu_pagas = numero_seguro(cliente_encontrado.get("paga")) * 1000
                ganancia = cliente_paga - tu_pagas
                correo = cliente_encontrado.get("correo", "No registrado")
                serial = cliente_encontrado.get("serial_antena", "No registrado")
                kit = cliente_encontrado.get("kit_antena", "")
                nota = cliente_encontrado.get("NOTA", "")
                
                mensaje = (
                    f"Información de {cliente_encontrado.get('cliente')}\n\n"
                    f"{estado}\n"
                    f"Vence: día {dia_vence}\n"
                    f"Cliente paga: {formato_pesos(cliente_paga)}\n"
                    f"Tú pagas: {formato_pesos(tu_pagas)}\n"
                    f"Ganancia: {formato_pesos(ganancia)}\n"
                    f"Corte del servicio: día {dia_vence}\n"
                    f"Correo: {correo}\n"
                    f"Serial antena: {serial}\n"
                    f"Kit: {kit}"
                )
                
                if nota:
                    mensaje += f"\nNota: {nota}"
                
                resp.message(mensaje)
                return str(resp)
            else:
                resp.message(f"❌ No se encontró ningún cliente con ese serial")
                return str(resp)

        # ================= AYUDA =================
        resp.message(
            "🤖 *Comandos disponibles:*\n"
            "- ping\n"
            "- pagos hoy\n"
            "- quien debe\n"
            "- proximos pagos\n"
            "- cuanto debe [nombre] (ej: cuanto debe juanes)\n"
            "- factura [nombre] (ej: factura juanes)\n"
            "- [nombre] pago (ej: juanes pago)\n"
            "- [serial] (ej: stk-392020-xx)"
        )

    except Exception as e:
        resp.message(f"❌ Error interno:\n{repr(e)}")

    return str(resp)

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
