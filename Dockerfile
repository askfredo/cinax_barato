FROM python:3.11-slim

RUN apt-get update && apt-get install -y libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia el bot, el modelo y nada más
COPY cinax_v03_gcs.py cinax_v03_gcs.py
COPY modelo.pkl modelo.pkl

CMD ["python", "cinax_v03_gcs.py"]
