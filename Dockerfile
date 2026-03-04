FROM n8nio/n8n:latest

# Cambiamos a root para instalar dependencias de sistema
USER root

# Instalamos Python, FFmpeg y librerías necesarias para procesamiento de video/audio
RUN apk add --no-cache \
    bash \
    python3 \
    py3-pip \
    ffmpeg \
    libsm \
    libxext \
    git

# Directorio de trabajo para nuestros scripts
WORKDIR /scripts

# Copiamos nuestros requerimientos de Python y los instalamos
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Copiamos el script de inteligencia artificial
COPY ai_editor.py .
RUN chmod +x ai_editor.py

# Volvemos al directorio de n8n
WORKDIR /home/node

# Volvemos al usuario de n8n por seguridad
USER node
