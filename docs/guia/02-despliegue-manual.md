# 02 · Despliegue manual

Este capítulo crea a mano todo lo que crea `terraform apply` en `infra/`. Los pasos van en orden de dependencia: cada uno necesita que exista lo anterior. Todos los comandos usan `gcloud` y `bq`, que vienen con el Google Cloud SDK.

Al final de cada paso hay una verificación. No conviene avanzar al siguiente si la verificación falla.

> Usa este capítulo **o** Terraform, no los dos sobre el mismo proyecto. Terraform no sabe nada de lo que se crea a mano, y un `terraform apply` posterior intentaría crear recursos que ya existen.

## Variables

Todos los comandos usan estas variables. Defínelas una vez en la terminal (bash / Git Bash):

```bash
PROJECT=review-pulse-dev        # reemplazar por el ID de tu proyecto
REGION=us-central1
BUCKET="$PROJECT-data"
SA_NAME=review-pulse-dataflow-runner
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX   # gcloud billing accounts list
```

Requisito previo: sesión iniciada con `gcloud auth login` y credenciales de aplicación con `gcloud auth application-default login`.

---

## Paso 1 · Proyecto y facturación

**Qué crea:** un proyecto vacío, vinculado a una cuenta de facturación.
**Por qué:** todo recurso vive dentro de un proyecto, y casi ninguna API funciona sin facturación. Un proyecto dedicado permite borrar todo sin afectar otros trabajos, y la alerta de presupuesto cuenta solo este gasto.
**Terraform:** ninguno; el proyecto se crea antes de Terraform.

```bash
gcloud projects create $PROJECT --name="Review Pulse"
gcloud billing projects link $PROJECT --billing-account=$BILLING_ACCOUNT
```

**Verificar:**
```bash
gcloud projects describe $PROJECT --format="value(lifecycleState)"          # ACTIVE
gcloud billing projects describe $PROJECT --format="value(billingEnabled)"  # True
```

**Consola:** selector de proyecto (arriba a la izquierda) → pestaña *Todos*. Un proyecto recién creado puede tardar en aparecer en el selector; `https://console.cloud.google.com/home/dashboard?project=$PROJECT` lo abre directo.

---

## Paso 2 · Habilitar APIs

**Qué crea:** habilita los servicios que se van a usar.
**Por qué:** cada servicio de GCP está deshabilitado por defecto en un proyecto nuevo. Habilitar Dataflow habilita también Compute Engine, que Dataflow necesita para sus workers.
**Terraform:** `google_project_service.fase1` en `infra/apis.tf`.

```bash
gcloud services enable pubsub.googleapis.com bigquery.googleapis.com storage.googleapis.com \
  dataflow.googleapis.com billingbudgets.googleapis.com --project=$PROJECT
```

**Verificar:**
```bash
gcloud services list --enabled --project=$PROJECT --format="value(config.name)" \
  | grep -E "pubsub|bigquery.googleapis|storage.googleapis|dataflow|compute|billingbudgets"
```

**Consola:** *APIs y servicios → APIs y servicios habilitados*.

---

## Paso 3 · Bucket de Cloud Storage

**Qué crea:** un bucket para la copia cruda (`raw/`), los rechazos (`dead-letter/`), el catálogo (`seed/`) y los archivos temporales de Dataflow (`tmp/`, `staging/`).
**Por qué:** un solo bucket con prefijos, porque ninguno necesita una política distinta. El acceso uniforme hace que los permisos se manejen solo con IAM, sin ACLs por objeto.
**Terraform:** `google_storage_bucket.review_pulse` en `infra/storage.tf`.

```bash
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access --project=$PROJECT
```

Los prefijos no se crean: en GCS una "carpeta" es solo parte del nombre del objeto, y aparece cuando se escribe el primer archivo.

**Verificar:**
```bash
gcloud storage buckets describe gs://$BUCKET --format="value(location,uniform_bucket_level_access)"
```

**Consola:** *Cloud Storage → Buckets*.

---

## Paso 4 · Topic y subscription de Pub/Sub

**Qué crea:** el topic `reviews` y la subscription `reviews-dataflow-sub`.
**Por qué:** el topic recibe los eventos del generador y la subscription los guarda hasta que Dataflow los confirma. `--ack-deadline=60` da un minuto para confirmar cada mensaje antes de que se reentregue. `--expiration-period=never` evita que Pub/Sub borre la subscription tras 31 días sin uso.
**Terraform:** `google_pubsub_topic.reviews` y `google_pubsub_subscription.reviews_dataflow` en `infra/pubsub.tf`.

```bash
gcloud pubsub topics create reviews --project=$PROJECT
gcloud pubsub subscriptions create reviews-dataflow-sub --topic=reviews \
  --ack-deadline=60 --expiration-period=never --project=$PROJECT
```

**Verificar:**
```bash
gcloud pubsub subscriptions describe reviews-dataflow-sub --project=$PROJECT \
  --format="value(topic,ackDeadlineSeconds,expirationPolicy)"
```
`expirationPolicy` debe aparecer vacío: eso significa "no expira".

**Consola:** *Pub/Sub → Temas* y *Pub/Sub → Suscripciones*.

---

## Paso 5 · Datasets de BigQuery

**Qué crea:** los datasets `bronze`, `silver` y `gold`, vacíos.
**Por qué:** separan las capas de datos. La ubicación (`us-central1`) no se puede cambiar después de creado el dataset.
**Terraform:** `google_bigquery_dataset.bronze`, `.silver` y `.gold` en `infra/bigquery.tf`.

```bash
for ds in bronze silver gold; do
  bq --location=$REGION mk --dataset $PROJECT:$ds
done
```

**Verificar:**
```bash
bq ls --project_id=$PROJECT
```

**Consola:** *BigQuery → Studio*, panel *Explorador*, dentro del proyecto.

---

## Paso 6 · Tabla `bronze.reviews_raw`

**Qué crea:** la tabla donde Dataflow escribe los eventos válidos.
**Por qué:** el pipeline no la crea (`CREATE_NEVER`), así que tiene que existir antes de lanzar el job. Particionada por día sobre `ingest_ts`, con filtro de partición obligatorio. El schema sale del mismo archivo JSON que usa el pipeline, así no pueden quedar desalineados.
**Terraform:** `google_bigquery_table.bronze_reviews_raw` en `infra/bigquery.tf`.

Desde la raíz del repo:

```bash
bq mk --table --time_partitioning_type=DAY --time_partitioning_field=ingest_ts \
  --require_partition_filter=true \
  $PROJECT:bronze.reviews_raw pipelines/dataflow/schemas/bronze_reviews_raw.json
```

**Verificar:**
```bash
bq show --format=prettyjson $PROJECT:bronze.reviews_raw | grep -E '"field"|"type": "DAY"|requirePartitionFilter'
```

Debe mostrar `"field": "ingest_ts"`, `"type": "DAY"` y `"requirePartitionFilter": true`.

---

## Paso 7 · Service account de Dataflow

**Qué crea:** la identidad con la que corren los workers.
**Por qué:** si no se indica una, Dataflow usa la cuenta por defecto de Compute Engine, que suele tener permisos amplios sobre todo el proyecto. Con una cuenta dedicada, los workers solo pueden hacer lo que se les otorga en el paso 8.
**Terraform:** `google_service_account.dataflow_runner` en `infra/iam.tf`.

```bash
gcloud iam service-accounts create $SA_NAME --display-name="Review Pulse - Dataflow runner" --project=$PROJECT
```

**Verificar:**
```bash
gcloud iam service-accounts describe $SA --project=$PROJECT --format="value(email)"
```

**Consola:** *IAM y administración → Cuentas de servicio*.

---

## Paso 8 · Permisos de la service account

**Qué crea:** seis permisos. Dos a nivel de proyecto, porque no existen por recurso, y cuatro limitados a un recurso.
**Por qué:** mínimo privilegio. La tabla del capítulo 01 (sección IAM) explica cada rol.
**Terraform:** los recursos `google_*_iam_member` en `infra/iam.tf`.

```bash
# Proyecto: no existe una versión por recurso de estos dos roles
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/dataflow.worker
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/bigquery.jobUser

# Subscription: consumir mensajes y leer la configuración
gcloud pubsub subscriptions add-iam-policy-binding reviews-dataflow-sub --project=$PROJECT \
  --member=serviceAccount:$SA --role=roles/pubsub.subscriber
gcloud pubsub subscriptions add-iam-policy-binding reviews-dataflow-sub --project=$PROJECT \
  --member=serviceAccount:$SA --role=roles/pubsub.viewer

# Bucket: escribir archivos
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$SA --role=roles/storage.objectAdmin

# Dataset bronze: escribir filas (con SQL, porque es la forma directa de otorgar un rol sobre un dataset)
bq query --project_id=$PROJECT --use_legacy_sql=false \
  "GRANT \`roles/bigquery.dataEditor\` ON SCHEMA \`$PROJECT.bronze\` TO 'serviceAccount:$SA'"
```

En BigQuery, *schema* es otro nombre para *dataset*.

**Verificar:**
```bash
gcloud projects get-iam-policy $PROJECT --flatten="bindings[].members" \
  --filter="bindings.members:$SA" --format="value(bindings.role)"          # dataflow.worker, bigquery.jobUser
gcloud pubsub subscriptions get-iam-policy reviews-dataflow-sub --project=$PROJECT --format="value(bindings.role)"
gcloud storage buckets get-iam-policy gs://$BUCKET --format="value(bindings.role)"
bq show --format=prettyjson $PROJECT:bronze | grep -B2 -A2 "$SA"
```

**Consola:** *IAM y administración → IAM* muestra los roles de proyecto. Los de recurso se ven en el panel *Permisos* de cada recurso (la subscription, el bucket o el dataset → *Compartir*).

---

## Paso 9 · Alerta de presupuesto (opcional)

**Qué crea:** un presupuesto mensual que envía correos al 50%, 90% y 100% del monto.
**Por qué:** es la red de seguridad por si un job queda encendido por error. Requiere permisos sobre la cuenta de facturación.
**Terraform:** `google_billing_budget.monthly` en `infra/billing.tf`.

```bash
gcloud billing budgets create --billing-account=$BILLING_ACCOUNT \
  --display-name="review-pulse monthly budget" --budget-amount=75 \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
  --filter-projects=projects/$PROJECT
```

El monto va **sin moneda**: la API exige que coincida con la moneda de la cuenta de facturación, y sin moneda la toma de la cuenta. En una cuenta en soles, `75` equivale a unos USD 20; en una cuenta en dólares, ajusta el valor.

**Verificar:**
```bash
gcloud billing budgets list --billing-account=$BILLING_ACCOUNT --format="value(displayName,amount.specifiedAmount.units)"
```

**Consola:** *Facturación → Presupuestos y alertas*.

---

## Qué falta para correr el pipeline

Nada más de infraestructura. El capítulo 04 cubre el entorno de Python y el lanzamiento. `scripts/run_dataflow.sh` toma los valores de `terraform output`; si la infraestructura se creó a mano, ejecuta directamente el comando `python pipelines/dataflow/pipeline.py` que aparece en ese script, reemplazando cada `$(tf ...)` por el valor correspondiente:

| `$(tf ...)` | Valor |
|---|---|
| `project_id` | `$PROJECT` |
| `region` | `$REGION` |
| `bucket_name` | `$BUCKET` |
| `dataflow_runner_email` | `$SA` |
| `pubsub_subscription` | `reviews-dataflow-sub` |
| `bronze_table` | `$PROJECT:bronze.reviews_raw` |

---

## Desmontaje manual

El orden importa. **Primero los jobs de Dataflow:** si se borran la subscription o el bucket con un job corriendo, el worker sigue encendido (y cobrando) mientras falla contra recursos que ya no existen.

```bash
# 1. Detener los jobs activos y esperar a que terminen
gcloud dataflow jobs list --project=$PROJECT --region=$REGION --status=active --format="value(id)"
gcloud dataflow jobs cancel <JOB_ID> --project=$PROJECT --region=$REGION

# 2. Presupuesto (si se creó)
gcloud billing budgets list --billing-account=$BILLING_ACCOUNT --format="value(name)"
gcloud billing budgets delete <BUDGET_NAME>

# 3. Datos y mensajería
bq rm -r -f -d $PROJECT:bronze
bq rm -r -f -d $PROJECT:silver
bq rm -r -f -d $PROJECT:gold
gcloud storage rm -r gs://$BUCKET
gcloud pubsub subscriptions delete reviews-dataflow-sub --project=$PROJECT
gcloud pubsub topics delete reviews --project=$PROJECT

# 4. Identidad (sus permisos de recurso desaparecen con los recursos borrados;
#    los de proyecto se quitan explícitamente)
gcloud projects remove-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/dataflow.worker
gcloud projects remove-iam-policy-binding $PROJECT --member=serviceAccount:$SA --role=roles/bigquery.jobUser
gcloud iam service-accounts delete $SA --project=$PROJECT
```

Las APIs pueden quedar habilitadas: habilitadas y sin uso no cuestan nada.

**Conserva el proyecto.** Un proyecto vacío no genera costos, y si se elimina, su ID queda bloqueado durante 30 días y no se puede reutilizar.
