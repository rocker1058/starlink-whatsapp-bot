FROM python:3.9

# Instalar LibreOffice, LaTeX y dependencias
RUN apt-get update && apt-get install -y \
    libreoffice-writer \
    texlive-xetex \
    texlive-latex-extra \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio de trabajo
WORKDIR /app

# Copiar requirements y instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código de la aplicación
COPY . .

# Exponer puerto
EXPOSE 10000

# Comando para ejecutar la aplicación
CMD ["python", "app.py"]
