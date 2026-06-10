#!/bin/bash
# Deploys CR Tournament Finder to Google Cloud Run.
# Output is mirrored to deploy.log so progress can be followed.
cd "$(dirname "$0")"
exec > >(tee deploy.log) 2>&1

echo "=== CR Tournament Finder — Cloud Run Deploy ==="
date

# Find gcloud (Homebrew installs may not be in PATH)
if command -v gcloud >/dev/null 2>&1; then
  GCLOUD="$(command -v gcloud)"
elif [ -x /opt/homebrew/share/google-cloud-sdk/bin/gcloud ]; then
  GCLOUD=/opt/homebrew/share/google-cloud-sdk/bin/gcloud
else
  echo "ERROR: gcloud not found. Install via 'brew install google-cloud-sdk'."
  exit 1
fi
echo "Using gcloud: $GCLOUD"

"$GCLOUD" run deploy cr-tournament-finder \
  --source . \
  --region europe-west3 \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 300 \
  --concurrency 10 \
  --min-instances 0 \
  --max-instances 1 \
  --execution-environment gen2 \
  --add-volume name=config,type=cloud-storage,bucket=cr-tournament-finder-config \
  --add-volume-mount volume=config,mount-path=/data \
  --set-env-vars "FLASK_ENV=production,CONFIG_PATH=/data/config.json,SEARCH_WORKERS=25,DETAIL_WORKERS=50,VERIFY_WORKERS=5,MAX_VERIFICATION_PASSES=2,QUERY_DRILLDOWN_THRESHOLD=20" \
  --set-secrets "CR_API_KEY=cr-api-key:latest,CR_FINDER_PASSWORD=cr-finder-password:latest,FLASK_SECRET_KEY=flask-secret-key:latest" \
  --quiet

RC=$?
echo "DEPLOY_EXIT=$RC"
if [ $RC -eq 0 ]; then
  echo "✅ Deployment successful: https://cr-tournament-finder-98463050344.europe-west3.run.app"
else
  echo "❌ Deployment failed (exit $RC) — see output above."
fi
