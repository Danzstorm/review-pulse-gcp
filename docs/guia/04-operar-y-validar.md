# 04 · Operar y validar

Una sesión completa, de principio a fin: preparar el entorno, publicar eventos, lanzar el pipeline, observarlo, comprobar que no se perdió nada y apagarlo.

Todos los comandos se ejecutan desde la raíz del repo, en bash (Git Bash en Windows). Los ejemplos usan la variable `PROJECT`:

```bash
PROJECT=$(terraform -chdir=infra output -raw project_id)   # o el ID a mano, si se desplegó sin Terraform
```

---

## 1. Entorno de Python (una sola vez)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r generator/requirements.txt -r pipelines/dataflow/requirements.txt
```

En Linux o macOS, la ruta es `.venv/bin/python`.

Además hace falta **Java** (un JRE; este proyecto se lanzó con Java 17) en la máquina que lanza el job: la escritura en BigQuery es un transform cross-language (ver capítulo 01). Se comprueba con `java -version`.

## 2. Tests

```bash
.venv/Scripts/python tests/test_generator.py   # 5 tests, sin GCP
.venv/Scripts/python tests/test_pipeline.py    # 7 tests, sin GCP (usa Beam en local)
```

Cada test imprime `ok <nombre>`. Si alguno falla, no tiene sentido lanzar el pipeline.

## 3. Publicar eventos

Primero, sin GCP, para ver qué se va a publicar:

```bash
.venv/Scripts/python generator/publish.py --dry-run --rate 60 --duration 1 --seed 7
```

Después, contra el topic real:

```bash
# Tráfico normal: 20 eventos por minuto durante 10 minutos
GOOGLE_CLOUD_QUOTA_PROJECT=$PROJECT .venv/Scripts/python generator/publish.py \
  --project $PROJECT --rate 20 --duration 10

# Escenario de incidente: entre el minuto 1 y el 4, la mitad de los eventos son
# reseñas negativas de conectividad para P-0001
GOOGLE_CLOUD_QUOTA_PROJECT=$PROJECT .venv/Scripts/python generator/publish.py \
  --project $PROJECT --rate 60 --duration 5 --incident-start 1 --incident-minutes 3 --seed 31
```

Se puede publicar antes de lanzar el pipeline: los mensajes esperan en la subscription hasta 7 días.

## 4. Lanzar el pipeline

```bash
bash scripts/run_dataflow.sh
```

El script envía el job y termina. La salida incluye `Submitted job: <JOB_ID>` y el enlace a la consola. **Desde ese momento el job cobra**, hasta que se detenga (paso 7).

Qué hace cada opción del script:

| Opción | Para qué |
|---|---|
| `GOOGLE_CLOUD_QUOTA_PROJECT=$PROJECT` | Atribuye las llamadas a la API a este proyecto, aunque el ADC tenga otro proyecto como quota project. Afecta solo a ese proceso. |
| `--runner=DataflowRunner` | Ejecutar en Dataflow y no en local |
| `--project`, `--region` | Dónde se crea el job. La región coincide con la del bucket y los datasets. |
| `--job_name=review-pulse-<fecha>` | Nombre único por lanzamiento; Dataflow no admite dos jobs activos con el mismo nombre |
| `--service_account_email` | Los workers corren con la cuenta dedicada y sus permisos mínimos, no con la cuenta por defecto de Compute Engine |
| `--temp_location`, `--staging_location` | Carpetas del bucket donde Dataflow deja archivos temporales y el código empaquetado del job |
| `--enable_streaming_engine` | El estado del streaming vive en el servicio de Google y no en el disco del worker |
| `--worker_machine_type=e2-standard-2` | Máquina chica y barata, con buena disponibilidad. El tipo por defecto no tenía capacidad en la zona (ver problemas). |
| `--max_num_workers=1` | Nunca más de una VM: control de costos |
| `--input_subscription`, `--bronze_table`, `--bucket` | Las opciones propias del pipeline (`ReviewOptions`) |

## 5. Observar el job

**Consola:** *Dataflow → Jobs → el job*. El grafo muestra cada paso con el nombre que tiene en el código (`ReadPubSub`, `ParseValidate`, `WriteBronze`, `RawWrite`, `DeadLetterWrite`…) y los elementos que pasaron por cada uno.

> El estado **Running** (verde) solo dice que el worker está vivo. Si un paso lanza una excepción, Dataflow reintenta el elemento indefinidamente y el job sigue en verde sin escribir nada. Lo que confirma que funciona son los datos que llegan a destino, no el color.

**Errores del job** (excluyendo el aviso de falta de capacidad, que Dataflow resuelve reintentando):

```bash
JOB=<JOB_ID>
gcloud logging read "resource.type=dataflow_step AND resource.labels.job_id=$JOB AND severity>=ERROR AND NOT textPayload:\"ZONE_RESOURCE_POOL_EXHAUSTED\"" \
  --project=$PROJECT --limit=10 --freshness=30m --format="value(timestamp,textPayload,jsonPayload.message)"
```

**Contadores del pipeline** (solo existe en `beta`):

```bash
gcloud beta dataflow metrics list $JOB --project=$PROJECT --region=us-central1 --source=user \
  --format="value(name.name,scalar)"
```

Muestra `valid`, un contador por motivo de rechazo y los contadores del conector de BigQuery (`recordsAppended`, `BigQuery-write-error-counter`).

**Filas en bronze** (la tabla exige filtrar por `ingest_ts`):

```bash
bq query --project_id=$PROJECT --use_legacy_sql=false \
  "SELECT COUNT(*) AS n FROM bronze.reviews_raw WHERE ingest_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)"
```

**Archivos en GCS:**

```bash
gcloud storage ls -r "gs://$PROJECT-data/raw/**" "gs://$PROJECT-data/dead-letter/**"
```

Los archivos de una ventana aparecen unos minutos **después** de que la ventana termina, cuando el watermark la cierra. Bronze, en cambio, recibe cada fila en segundos.

## 6. Conciliación: comprobar que no se perdió nada

Mirar la consola no alcanza para asegurar que llegó todo. El método:

1. **Entrada determinista.** El generador, con la misma `--seed` y los mismos argumentos, produce siempre los mismos eventos.
2. **Resultado esperado antes de correr.** Esos mismos eventos se pasan en local por `validate()`, la misma función que usa el pipeline. Así se sabe cuántos válidos y cuántos rechazos de cada tipo **deben** aparecer.
3. **Tres mediciones independientes:**
   - los contadores de Dataflow (lo que el pipeline clasificó);
   - las filas en bronze y las líneas en `raw/` (lo que llegó del lado válido);
   - las líneas en `dead-letter/`, agrupadas por motivo (lo que llegó del lado rechazado).

Si las tres coinciden con lo esperado, número por número, no se perdió ni se inventó nada. Si alguna difiere, la diferencia indica en qué tramo está el problema: así se detectó la pérdida de 8 rechazos en la dead-letter.

**Con un comando:** `scripts/reconcile.py` hace todo el procedimiento e imprime una tabla (esperado | contadores | destino). Termina con código 1 si algo no coincide.

```bash
.venv/Scripts/python scripts/reconcile.py --since 2026-09-23T18:50:00Z --job <JOB_ID> -- \
  --rate 60 --duration 5 --incident-start 1 --incident-minutes 3 --seed 31
```

- `--since`: un instante UTC justo **antes** de empezar a publicar, para contar solo las filas de esta corrida.
- Lo que va después de `--` son los mismos argumentos que se le pasaron al generador.
- Los contadores son por job: conviene publicar una sola tanda por job para que sean comparables.

**Resultado real** (semilla 31, 300 eventos, job `2026-09-23_11_50_25-18342198976324619151`):

| Resultado | Esperado | Contadores de Dataflow | Llegó a destino |
|---|---|---|---|
| válidos | 288 | 288 | 288 en bronze · 288 líneas en `raw/` |
| `malformed_json` | 4 | 4 | 4 |
| `missing_fields` | 3 | 3 | 3 |
| `invalid_rating` | 4 | 4 | 4 |
| `invalid_event_ts` | 1 | 1 | 1 |

Además: un archivo por ventana (4 en total), `recordsAppended = 288` y `BigQuery-write-error-counter = 0`. Las 288 filas de bronze tienen 279 `review_id` distintos: los 9 duplicados del productor llegan a bronze a propósito, porque la deduplicación ocurre en silver.

## 7. Apagar

```bash
bash scripts/stop.sh            # drain: termina lo que está en vuelo y se detiene
bash scripts/stop.sh --cancel   # cancel: se detiene de inmediato
bash scripts/down.sh            # cancel + terraform destroy: todo a costo cero
```

`stop.sh` espera hasta que no quede ningún job activo, así que cuando termina, Dataflow ya no cobra. El drain es la opción normal al cerrar una sesión. El cancel sirve cuando el job está roto o antes de destruir la infraestructura. Lo que no llegó a confirmarse vuelve a Pub/Sub, así que no se pierde.

`down.sh` cancela los jobs **antes** del `terraform destroy`, porque Terraform no administra el job: un `destroy` solo dejaría el worker encendido.

---

## Resolución de problemas

Todos estos errores ocurrieron de verdad durante la construcción del pipeline.

| Síntoma | Causa | Solución |
|---|---|---|
| Al lanzar: `403 Dataflow API has not been used in project <otro-proyecto> … SERVICE_DISABLED` | El ADC tiene otro proyecto como *quota project*, y las llamadas se atribuyen a ese proyecto, donde Dataflow no está habilitado | Definir `GOOGLE_CLOUD_QUOTA_PROJECT=<proyecto>` para el proceso (ya lo hace `run_dataflow.sh`). Evitar `set-quota-project`, que cambia el ADC para todo. |
| En los logs: `ZONE_RESOURCE_POOL_EXHAUSTED`; el job sigue sin workers y a veces termina en `Failed` | Google no tiene capacidad para ese tipo de máquina en esa zona (stockout). No es un problema de cuota. | Dataflow reintenta en otra zona. Usar `e2-standard-2`, que suele tener más disponibilidad. Si persiste, relanzar más tarde. |
| Advertencia: `Querying the configuration of Pub/Sub subscription … failed` | `pubsub.subscriber` no incluye `pubsub.subscriptions.get` | Otorgar `roles/pubsub.viewer` sobre la subscription |
| Job en verde, 0 filas; en los logs: `'DatetimeWithNanoseconds' object has no attribute 'to_utc_datetime'` | El código trataba `publish_time` como `Timestamp` de Beam, pero Dataflow entrega un `datetime` | Normalizar con `datetime.fromtimestamp(...)` (ver `ParseAndValidate`). El test usa ahora el tipo real. |
| Job en verde, 0 filas; en los logs: `'datetime.datetime' object has no attribute 'micros' [while running 'WriteBronze/…/Convert dict to Beam Row']` | El conector de la Storage Write API necesita `Timestamp` de Beam en las columnas `TIMESTAMP` | Convertir con `to_bq_row` antes de `WriteToBigQuery` |
| En local: `Streaming Python direct runner does not support cross-language pipelines` | El DirectRunner no soporta el conector Java de BigQuery en streaming | No hay runner local para el grafo completo. Probar la lógica con tests y la integración en Dataflow. |
| En local con `--runner=PrismRunner`: `unsupported feature … beam:transform:pubsub_read:v1` | PrismRunner no soporta la lectura nativa de Pub/Sub | Igual que el anterior |
| La dead-letter tenía 2 de 10 rechazos esperados, sin ningún error en los logs | `fileio.WriteToFiles` numeraba archivos desde 0 en cada grupo; dos grupos de la misma ventana produjeron el mismo nombre y uno sobrescribió al otro | Reemplazado por `GroupByKey` + `WriteWindowFile`, con un archivo por ventana y pane. La conciliación lo detectó. |
| `bq query`: `Syntax error: Unexpected keyword ROWS` | `rows` es palabra reservada en BigQuery SQL | Usar otro alias (`n`, `n_rows`) |
| `gcloud dataflow metrics list`: `Invalid choice: 'metrics'` | El comando solo existe en el canal beta | `gcloud beta dataflow metrics list` |
| El proyecto no aparece en el selector de la consola ni en `gcloud projects list`, pero `gcloud projects describe` sí lo encuentra | El listado sale de un índice que se actualiza con retraso | Entrar con el enlace directo `?project=<proyecto>` y esperar a que el índice se actualice |
