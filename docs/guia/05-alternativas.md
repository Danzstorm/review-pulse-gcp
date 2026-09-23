# 05 · Alternativas: otros caminos para el streaming

El pipeline de Dataflow no es la única forma de llevar mensajes de Pub/Sub a BigQuery. Este capítulo resume las alternativas y documenta un experimento que corre **en paralelo** al pipeline principal, con los mismos mensajes, para compararlos con datos reales.

---

## 1. El mapa de opciones

| Camino | Cómo funciona | Cuándo conviene | Costo sin tráfico |
|---|---|---|---|
| **Pub/Sub → BigQuery subscription** | Pub/Sub escribe cada mensaje directo en una tabla. Sin código ni workers | Aterrizar datos tal cual y transformar después en SQL (ELT) | ~0 |
| **Pub/Sub → Cloud Storage subscription** | Pub/Sub escribe archivos en GCS por lotes de tiempo o tamaño | La copia cruda para reprocesar | ~0 |
| **Plantilla de Dataflow de Google** | Job gestionado sin escribir Beam; se transforma con una UDF y los errores van a una tabla | Transformaciones simples sin pipeline propio | igual que Dataflow |
| **Dataflow con Beam propio** *(el principal)* | Control total: validación, ventanas, estado, varios destinos | Lógica por evento compleja, estado, alto volumen | worker encendido |
| **Cloud Run con push subscription** | Pub/Sub llama a un servicio HTTP por mensaje; el servicio escala a cero | Volumen bajo o medio, lógica simple, sin ventanas | ~0 |
| **Spark Streaming en Dataproc** | Un clúster de Spark | Equipos que ya trabajan con Spark | clúster encendido |

**La idea que habilita el experimento:** un topic puede tener varias subscriptions, y **cada una recibe su propia copia** de cada mensaje. Agregar una subscription no le quita nada al pipeline existente.

---

## 2. El experimento: ELT con una BigQuery subscription

```
                      ┌─ reviews-dataflow-sub ──▶ Dataflow ──▶ bronze.reviews_raw (+ raw/, dead-letter/)
topic "reviews" ──────┤
                      └─ reviews-bigquery-sub ──▶ bronze.reviews_raw_bqsub ──▶ vista bronze.reviews_bqsub_classified
                           (Pub/Sub escribe directo)     (todo crudo, incluso lo inválido)   (validación en SQL)
```

### Qué se crea

Todo está en `infra/experiment_bq_subscription.tf`. Si se borra ese archivo y se aplica, el experimento desaparece.

| Recurso | Detalle |
|---|---|
| Tabla `bronze.reviews_raw_bqsub` | Columnas que exige una BigQuery subscription con metadatos: `subscription_name`, `message_id` (STRING), `publish_time` (TIMESTAMP), `data` (STRING) y `attributes` (STRING). Particionada por `publish_time` |
| Permiso | `roles/bigquery.dataEditor` para el service agent de Pub/Sub (`service-NÚMERO_DE_PROYECTO@gcp-sa-pubsub.iam.gserviceaccount.com`), **solo sobre esta tabla** |
| Subscription `reviews-bigquery-sub` | `bigquery_config` apuntando a la tabla, con `write_metadata = true` y sin expiración |
| Vista `bronze.reviews_bqsub_classified` | `sql/experiments/bqsub_classified.sql`: clasifica cada mensaje con las mismas reglas y en el mismo orden que `validate()` en Python |

**Por qué `data` es STRING y no JSON:** con una columna de tipo JSON, BigQuery rechaza los mensajes que no son JSON válido. Con STRING aterriza todo, incluso lo mal formado, y la clasificación ocurre en SQL. Es el principio de ELT: primero se carga, después se transforma.

### Despliegue manual

```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format="value(projectNumber)")
PUBSUB_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

# 1. Tabla con el layout que exige la subscription
bq mk --table --time_partitioning_type=DAY --time_partitioning_field=publish_time \
  $PROJECT:bronze.reviews_raw_bqsub \
  subscription_name:STRING,message_id:STRING,publish_time:TIMESTAMP,data:STRING,attributes:STRING

# 2. Permiso de escritura solo sobre esa tabla
bq query --use_legacy_sql=false \
  "GRANT \`roles/bigquery.dataEditor\` ON TABLE \`$PROJECT.bronze.reviews_raw_bqsub\` TO 'serviceAccount:$PUBSUB_AGENT'"

# 3. Subscription (Pub/Sub verifica el permiso al crearla: el paso 2 va antes)
gcloud pubsub subscriptions create reviews-bigquery-sub --topic=reviews \
  --bigquery-table=$PROJECT:bronze.reviews_raw_bqsub --write-metadata --expiration-period=never

# 4. Vista de validación (reemplazar ${project} por el ID del proyecto)
sed "s/\${project}/$PROJECT/" sql/experiments/bqsub_classified.sql > /tmp/view.sql
bq mk --use_legacy_sql=false --view "$(cat /tmp/view.sql)" $PROJECT:bronze.reviews_bqsub_classified
```

Verificación: `gcloud pubsub subscriptions describe reviews-bigquery-sub --format="value(bigqueryConfig.state)"` debe devolver `ACTIVE`.

Para desmontarlo a mano, primero se borra la subscription, así deja de escribir, y después la vista y la tabla:

```bash
gcloud pubsub subscriptions delete reviews-bigquery-sub --project=$PROJECT
bq rm -f $PROJECT:bronze.reviews_bqsub_classified
bq rm -f $PROJECT:bronze.reviews_raw_bqsub
```

Mientras exista, esta subscription recibe una copia de **cada** publicación, aunque Dataflow esté apagado. Su costo es por volumen escrito, así que sin publicaciones no cuesta nada.

### La validación en SQL

La vista replica cada regla de `validate()` en el mismo orden: JSON mal formado → no es un objeto → campos faltantes → rating → tipos → `event_ts`. Antes de crearla se probó con filas escritas a mano, y los 10 casos borde dieron el mismo resultado que en Python.

La prueba encontró **la debilidad central del enfoque ELT.** `SAFE_CAST('2026-09-23T14:32:10' AS TIMESTAMP)` acepta un timestamp sin zona horaria y asume UTC, mientras que la regla en Python lo rechaza. Sin una expresión regular adicional que exija `Z` o un desfase horario, el camino SQL habría aceptado en silencio datos que el camino Python rechaza. **La misma regla escrita en dos lenguajes se desalinea sin avisar**, y solo una prueba explícita de paridad lo detecta.

---

## 3. Resultados

Una sola publicación (semilla 47, 300 eventos, con el escenario de incidente) llega a las dos subscriptions. `scripts/reconcile.py --bq-subscription` compara las cuatro mediciones:

```
outcome               expected    counters      landed  bq_sub_sql
valid                      289         289         289         289
malformed_json               2           2           2           2
missing_fields               3           3           3           3
invalid_rating               2           2           2           2
invalid_event_ts             4           4           4           4

raw/ lines 289 vs bronze rows 289
RECONCILED
```

| Dimensión | Dataflow (Beam propio) | BigQuery subscription + SQL |
|---|---|---|
| **Exactitud** | 289 / 11, igual a lo esperado | 289 / 11, igual a lo esperado |
| **Latencia en esta corrida** | El job tardó ~3 minutos en conseguir una VM (stockout en `us-central1-a`, tres intentos). Al terminar de publicar, bronze tenía 0 filas | Al terminar de publicar, las 300 filas ya estaban en la tabla |
| **Costo sin tráfico** | Mientras el job corre, el worker cobra por hora aunque no lleguen mensajes | ~0: se cobra por volumen escrito |
| **Código a mantener** | Pipeline, tests, schema compartido, Java para el transform cross-language, scripts de lanzamiento y apagado | ~60 líneas de Terraform y ~40 de SQL |
| **Dónde se rechaza lo inválido** | Antes de aterrizar: bronze solo tiene datos válidos | Después: la tabla tiene todo, y la vista clasifica |
| **Copia cruda en GCS** | Incluida (`raw/`, `dead-letter/`) | Haría falta una Cloud Storage subscription aparte |
| **Paridad de reglas** | Una sola implementación de las reglas | Reglas duplicadas en SQL: riesgo de desalinearse (caso `event_ts`) |
| **Transformaciones con estado o ventanas** | Sí | No, solo lo que se pueda expresar en SQL después |

### Lectura

A este volumen (decenas de eventos por minuto) y con validaciones que se pueden expresar en SQL, **el camino ELT es más simple, más barato y más rápido de arrancar**, con la misma exactitud. Dataflow se justifica cuando la lógica por evento no cabe en SQL (estado, ventanas, llamadas externas, enriquecimientos), cuando el volumen hace que el costo por byte de la subscription supere al de los workers, o cuando los datos inválidos no deben aterrizar en ningún lugar consultable.

En este proyecto se mantienen los dos: Dataflow es el camino principal porque su construcción es parte del objetivo, y la BigQuery subscription queda como referencia medida del camino alternativo.

---

## 4. Otras alternativas que no se probaron

- **Pub/Sub schema en el topic.** Asociar un schema Avro o Protobuf al topic rechaza los mensajes mal formados al publicarlos, antes de que entren al sistema. En producción es la primera barrera. Aquí se omite a propósito, para que los errores lleguen a los caminos de rechazo y se puedan demostrar.
- **Cloud Storage subscription.** Reemplazaría el escritor de `raw/` del pipeline por configuración: Pub/Sub escribe los archivos por lotes de tiempo o tamaño.
- **Cloud Run con push subscription.** Serviría si la lógica por mensaje necesita código (por ejemplo, una llamada a otra API) y el volumen es bajo: escala a cero y no tiene arranque de workers.
