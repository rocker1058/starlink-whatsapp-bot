from flask import Flask, request

app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    mensaje = request.form.get("Body")
    return "Bot activo ✅"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
