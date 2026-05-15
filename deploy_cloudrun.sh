#!/bin/bash
# deploy_cloudrun.sh
# Ejecuta esto UNA VEZ para configurar todo en Google Cloud
# Requisito: tener gcloud instalado y autenticado (gcloud auth login)

set -e  # para si algo falla

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — cambia estos valores
# ══════════════════════════════════════════════════════════════
PROJECT_ID="tu-proyecto-gcp"          # gcloud projects list para verlo
REGION="us-central1"                  # región más barata
JOB_NAME="cinax-bot"
IMAGE="gcr.io/$PROJECT_ID/$JOB_NAME"
BUCKET_NAME="cinax-data-$PROJECT_ID"  # nombre único del bucket
DISCORD_WEBHOOK="https://discord.com/api/webhooks/TU_WEBHOOK_AQUI"

# ══════════════════════════════════════════════════════════════
# 1. Habilitar APIs necesarias
# ══════════════════════════════════════════════════════════════
echo "→ Habilitando APIs..."
gcloud services enable run.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    storage.googleapis.com \
    --project=$PROJECT_ID

# ══════════════════════════════════════════════════════════════
# 2. Crear bucket GCS (si no existe)
# ══════════════════════════════════════════════════════════════
echo "→ Creando bucket GCS..."
gsutil mb -l $REGION gs://$BUCKET_NAME 2>/dev/null || echo "   (bucket ya existe)"

# ══════════════════════════════════════════════════════════════
# 3. Build y push de la imagen Docker
# ══════════════════════════════════════════════════════════════
echo "→ Construyendo imagen Docker..."
gcloud builds submit --tag $IMAGE --project=$PROJECT_ID .

# ══════════════════════════════════════════════════════════════
# 4. Crear el Cloud Run Job
# ══════════════════════════════════════════════════════════════
echo "→ Creando Cloud Run Job..."
gcloud run jobs create $JOB_NAME \
    --image=$IMAGE \
    --region=$REGION \
    --memory=1Gi \
    --cpu=1 \
    --max-retries=1 \
    --task-timeout=900 \
    --set-env-vars="DISCORD_WEBHOOK=$DISCORD_WEBHOOK,GCS_BUCKET=$BUCKET_NAME" \
    --project=$PROJECT_ID

# ══════════════════════════════════════════════════════════════
# 5. Crear el Cloud Scheduler
# Dispara de Lunes a Viernes a las 21:10 UTC (4:10 PM ET en verano)
# En invierno el mercado cierra a las 21:00 UTC (ajusta a 21:15 si quieres margen)
# ══════════════════════════════════════════════════════════════
echo "→ Creando Cloud Scheduler..."

# Obtener el service account por defecto
SA=$(gcloud iam service-accounts list \
    --filter="displayName:Compute Engine default service account" \
    --format="value(email)" \
    --project=$PROJECT_ID)

gcloud scheduler jobs create http cinax-trigger \
    --location=$REGION \
    --schedule="10 21 * * 1-5" \
    --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB_NAME:run" \
    --message-body="{}" \
    --oauth-service-account-email=$SA \
    --project=$PROJECT_ID

echo ""
echo "══════════════════════════════════════════════════"
echo "✓ Deploy completo!"
echo ""
echo "Bucket GCS  : gs://$BUCKET_NAME"
echo "Job         : $JOB_NAME"
echo "Scheduler   : Lun-Vie 21:10 UTC (4:10 PM ET)"
echo ""
echo "Para ejecutar manualmente:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT_ID"
echo ""
echo "Para ver logs:"
echo "  gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=$JOB_NAME' --limit=50 --project=$PROJECT_ID"
echo "══════════════════════════════════════════════════"
