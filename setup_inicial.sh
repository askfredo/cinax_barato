#!/bin/bash
# setup_inicial.sh
# Este script lo corres UNA SOLA VEZ desde Google Cloud Shell
# (el navegador, no necesitas instalar nada en tu PC)
# Accede en: https://shell.cloud.google.com

# ══════════════════════════════════════════════════════════════
# CAMBIA ESTOS VALORES ANTES DE CORRER
# ══════════════════════════════════════════════════════════════
PROJECT_ID="tu-proyecto-id"        # lo ves en console.cloud.google.com
REGION="us-central1"
BUCKET_NAME="cinax-data-$PROJECT_ID"
DISCORD_WEBHOOK="https://discord.com/api/webhooks/TU_WEBHOOK"

# ══════════════════════════════════════════════════════════════
# 1. Apuntar al proyecto correcto
# ══════════════════════════════════════════════════════════════
gcloud config set project $PROJECT_ID

# ══════════════════════════════════════════════════════════════
# 2. Habilitar las APIs que necesitamos
# ══════════════════════════════════════════════════════════════
echo "→ Habilitando APIs (tarda ~1 min)..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    storage.googleapis.com \
    iam.googleapis.com \
    containerregistry.googleapis.com

# ══════════════════════════════════════════════════════════════
# 3. Crear bucket GCS para los CSVs
# ══════════════════════════════════════════════════════════════
echo "→ Creando bucket GCS..."
gsutil mb -l $REGION gs://$BUCKET_NAME 2>/dev/null || echo "   (ya existe)"

# ══════════════════════════════════════════════════════════════
# 4. Crear Service Account para GitHub Actions
# ══════════════════════════════════════════════════════════════
echo "→ Creando Service Account para GitHub Actions..."
SA_NAME="cinax-github-sa"
SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME \
    --display-name="CINAX GitHub Actions SA" 2>/dev/null || echo "   (ya existe)"

# Darle los permisos necesarios
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/storage.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/iam.serviceAccountUser"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SA_EMAIL" \
    --role="roles/containerregistry.ServiceAgent"

# Permiso para subir imágenes a Container Registry
gsutil iam ch serviceAccount:$SA_EMAIL:objectAdmin gs://artifacts.$PROJECT_ID.appspot.com 2>/dev/null || true

# ══════════════════════════════════════════════════════════════
# 5. Generar la clave JSON para GitHub Secrets
# ══════════════════════════════════════════════════════════════
echo "→ Generando clave JSON..."
gcloud iam service-accounts keys create cinax-sa-key.json \
    --iam-account=$SA_EMAIL

echo ""
echo "══════════════════════════════════════════════════"
echo "✓ Clave generada en: cinax-sa-key.json"
echo ""
echo "COPIA el contenido de ese archivo:"
echo ""
cat cinax-sa-key.json
echo ""
echo "══════════════════════════════════════════════════"

# ══════════════════════════════════════════════════════════════
# 6. Crear el Cloud Run Job inicial (vacío, GitHub Actions lo actualiza después)
# ══════════════════════════════════════════════════════════════
echo "→ Creando Cloud Run Job inicial..."

# Usamos una imagen pública mínima para crearlo (GitHub Actions la reemplaza)
gcloud run jobs create cinax-bot \
    --image=gcr.io/google-containers/pause:latest \
    --region=$REGION \
    --memory=1Gi \
    --cpu=1 \
    --max-retries=1 \
    --task-timeout=900 \
    --set-env-vars="DISCORD_WEBHOOK=$DISCORD_WEBHOOK,GCS_BUCKET=$BUCKET_NAME" \
    2>/dev/null || echo "   (job ya existe)"

# ══════════════════════════════════════════════════════════════
# 7. Crear Cloud Scheduler
# Lun-Vie a las 21:10 UTC = 4:10 PM ET (horario verano)
# ══════════════════════════════════════════════════════════════
echo "→ Creando Cloud Scheduler..."

# Service account de Compute Engine (el default para invocar jobs)
DEFAULT_SA=$(gcloud iam service-accounts list \
    --filter="email~compute" \
    --format="value(email)" \
    --limit=1)

gcloud scheduler jobs create http cinax-trigger \
    --location=$REGION \
    --schedule="10 21 * * 1-5" \
    --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/cinax-bot:run" \
    --message-body="{}" \
    --oauth-service-account-email=$DEFAULT_SA \
    2>/dev/null || echo "   (scheduler ya existe)"

echo ""
echo "══════════════════════════════════════════════════"
echo "✓ Setup completo!"
echo ""
echo "Ahora ve a tu repo de GitHub:"
echo "Settings → Secrets and variables → Actions → New secret"
echo ""
echo "Agrega estos 2 secrets:"
echo "  GCP_PROJECT_ID  →  $PROJECT_ID"
echo "  GCP_CREDENTIALS →  (pega el JSON que aparece arriba)"
echo ""
echo "Luego haz git push y el bot se despliega solo."
echo "══════════════════════════════════════════════════"

# Limpiar la clave del disco por seguridad
rm cinax-sa-key.json
