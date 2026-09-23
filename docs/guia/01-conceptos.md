# 01 · Conceptos

Cada concepto está explicado en función de cómo lo usa Review Pulse. Al final hay un recorrido de un evento, salto por salto.

---

## Pub/Sub

Pub/Sub es un buffer de mensajes gestionado. Separa a quien produce los eventos (el generador) de quien los procesa (Dataflow): el productor publica sin saber quién va a leer, y si el consumidor está caído, los mensajes esperan.

### Topic y subscription

| Pieza | Qué es | En este proyecto |
|---|---|---|
| **Topic** | El canal donde se publica. No guarda mensajes por sí mismo. | `reviews` |
| **Subscription** | Una "cola" asociada a un topic. Cada subscription recibe **una copia** de cada mensaje publicado después de su creación, y lo guarda hasta que alguien lo confirme. | `reviews-dataflow-sub`, la lee Dataflow |

Si mañana otro sistema necesitara los mismos eventos, se le crearía otra subscription al mismo topic; ninguno afectaría al otro.

### El mensaje

| Campo | Quién lo pone | Uso aquí |
|---|---|---|
| `data` | El productor | Los bytes del JSON de la reseña, en UTF-8 |
| `attributes` | El productor (opcional) | No se usan |
| `message_id` | Pub/Sub, al aceptar el mensaje | Se guarda en bronze para distinguir tipos de duplicado |
| `publish_time` | Pub/Sub, al aceptar el mensaje | Se usa como `ingest_ts` (hora de ingesta) |

### Ack, ack deadline y entrega at-least-once

1. El consumidor recibe un mensaje.
2. Tiene un plazo (**ack deadline**, 60 s en la subscription) para confirmarlo con un **ack**.
3. Si lo confirma, Pub/Sub lo borra de la subscription. Si no lo confirma a tiempo, Pub/Sub lo vuelve a entregar.

Consecuencia: un mensaje nunca se pierde por una caída del consumidor, pero **puede llegar más de una vez** (un worker lo procesó y se cayó antes de confirmarlo). Esto se llama entrega **at-least-once**. Por eso el sistema asume duplicados y los resuelve más adelante, en silver.

Hay dos tipos de duplicado, y bronze permite distinguirlos:

| Caso | `review_id` | `message_id` | Origen |
|---|---|---|---|
| Reintento del productor | igual | distinto | el generador publicó dos veces la misma reseña |
| Reentrega de Pub/Sub | igual | igual | el consumidor no confirmó a tiempo |

### Retención y expiración

- **Retención (7 días):** un mensaje no confirmado espera hasta 7 días en la subscription. Si Dataflow está apagado, los eventos se acumulan y se procesan al volver a encenderlo.
- **Expiración:** por defecto, Pub/Sub **borra la subscription** si pasa 31 días sin actividad. Aquí está configurada como `never`, porque el proyecto pasa semanas sin correr.

---

## Apache Beam y Dataflow

**Beam** es la librería (el SDK) con la que se escribe el pipeline. **Dataflow** es el servicio de Google que lo ejecuta en máquinas gestionadas. El mismo código Beam podría correr en otro motor; a ese motor se le llama **runner**.

### Las piezas de un pipeline

| Concepto | Qué es | En `pipeline.py` |
|---|---|---|
| **Pipeline** | El grafo completo de pasos | `p = beam.Pipeline(options=options)` |
| **PCollection** | Un conjunto de elementos que fluye entre pasos. En streaming no tiene fin. | los mensajes leídos, `results.valid`, `results.invalid` |
| **PTransform** | Un paso del grafo | `ReadFromPubSub`, `WindowInto`, `GroupByKey`, `WriteToBigQuery` |
| **DoFn** | Una clase con la lógica que se aplica a cada elemento | `ParseAndValidate`, `WriteWindowFile` |
| **ParDo** | El transform que aplica un DoFn a cada elemento, en paralelo | `beam.ParDo(ParseAndValidate())` |
| **Tagged outputs** | Un DoFn que emite hacia más de una salida | válidos a la salida principal, rechazados a la salida `"invalid"` |

El operador `|` encadena pasos y `"Nombre" >> transform` le pone nombre al paso; ese nombre es el que aparece en el grafo de la consola de Dataflow.

### Ventanas, watermark y panes

En streaming los datos no terminan nunca, así que no se puede "agrupar todo". Una **ventana** corta el flujo en tramos finitos.

- **FixedWindows(300):** tramos fijos de 5 minutos (`17:50–17:55`, `17:55–18:00`, …). Cada elemento cae en la ventana que corresponde a su timestamp; aquí, el `publish_time` de Pub/Sub.
- **Watermark:** la estimación que hace el runner de "ya no van a llegar más elementos anteriores a esta hora". Cuando el watermark pasa el final de una ventana, la ventana se considera completa.
- **GroupByKey:** junta todos los elementos con la misma clave **dentro de una ventana**. Con la configuración por defecto emite el grupo una vez, cuando el watermark cierra la ventana.
- **Pane:** cada emisión de un grupo para una ventana. Con el trigger por defecto hay un solo pane por ventana, el `p0`. Si se configurara un trigger que emite varias veces, habría `p1`, `p2`, etc.

Así se escribe un archivo por ventana: todas las líneas de la ventana se agrupan con una sola clave, y el grupo completo se escribe una vez, con un nombre que depende de la ventana y del pane.

### Runner, SDK, workers y Streaming Engine

| Pieza | Qué hace |
|---|---|
| **SDK (Beam para Python)** | Construye el grafo en la máquina que lanza el job y lo envía a Dataflow |
| **Runner (DataflowRunner)** | El servicio que recibe el grafo, arranca máquinas y ejecuta los pasos |
| **Workers** | VMs de Compute Engine creadas por Dataflow. Aquí: 1 worker `e2-standard-2`, con la service account `review-pulse-dataflow-runner` |
| **Streaming Engine** | Mueve el estado del streaming (ventanas, agrupaciones, checkpoints) de los workers a un servicio gestionado. Workers más livianos, escalado más rápido. |

Mientras el job corre, el worker es una VM que cobra por hora, procese datos o no.

### Transforms cross-language (por qué hace falta Java)

La escritura en BigQuery mediante la **Storage Write API** está implementada en Java, no en Python. Beam la ofrece a Python como **transform cross-language**: al construir el pipeline, el SDK de Python arranca un servicio de expansión Java (un JAR) que traduce ese paso. Por eso la máquina que lanza el job necesita un **JRE instalado**. En Dataflow, ese paso corre en un contenedor Java junto al de Python.

### Por qué un job de streaming nunca "termina"

Un pipeline batch lee un conjunto finito y termina. Uno de streaming lee de Pub/Sub, que no tiene fin, así que el job queda corriendo hasta que alguien lo detiene. Dos consecuencias:

- El comando que lanza el job no debe esperar a que termine (se quedaría colgado para siempre). `run()` envía el job y devuelve el control.
- Apagarlo es una acción explícita, y **Terraform no lo hace**: el job no es un recurso de Terraform.

### Drain y cancel

| Acción | Qué pasa | Cuándo usarla |
|---|---|---|
| **Drain** | Deja de leer mensajes nuevos, termina de procesar lo que ya tiene y cierra las ventanas abiertas | Al terminar una sesión normal (`scripts/stop.sh`) |
| **Cancel** | Detiene todo de inmediato. Lo que estaba en vuelo sin confirmar vuelve a Pub/Sub. | Antes de destruir la infraestructura, o si el job está roto (`scripts/stop.sh --cancel`) |

---

## BigQuery

| Concepto | Qué es | Aquí |
|---|---|---|
| **Dataset** | Un contenedor de tablas con una ubicación fija | `bronze`, `silver`, `gold` en `us-central1` |
| **Tabla particionada** | La tabla se divide en particiones por día según una columna. Una consulta que filtra por esa columna solo lee las particiones necesarias y paga solo por ellas. | `bronze.reviews_raw`, particionada por `DATE(ingest_ts)` |
| **`require_partition_filter`** | BigQuery rechaza cualquier consulta sobre la tabla que no filtre por la columna de partición | Activado: evita escanear la tabla completa por error |
| **Storage Write API** | La forma recomendada de escribir en streaming: un canal gRPC que agrega filas a la tabla, consultables en segundos | Usada en modo **at-least-once** |

**At-least-once vs exactly-once:** el modo exactly-once garantiza que cada fila se escriba una sola vez, a cambio de un paso extra de coordinación con más costo y latencia. Aquí no hace falta: los duplicados ya existen desde Pub/Sub y el MERGE de silver los elimina. Pagar dos veces por la misma garantía no aporta nada.

Particionar bronze por `ingest_ts` (cuándo entró el evento) y no por `event_ts` (cuándo se escribió la reseña) hace que una carga incremental solo tenga que leer las particiones recientes, aunque lleguen reseñas con horas de atraso.

---

## IAM

### Service account

Una identidad para software, no para personas. Los workers de Dataflow corren como `review-pulse-dataflow-runner@<proyecto>.iam.gserviceaccount.com`, y solo pueden hacer lo que esa cuenta tiene permitido.

### Roles por recurso y por proyecto

Un rol otorgado **sobre el proyecto** vale para todos los recursos del proyecto. Uno otorgado **sobre un recurso** (un bucket, un dataset, una subscription) vale solo para ese recurso. El principio de mínimo privilegio pide usar el más acotado posible.

| Rol | Alcance | Por qué ese alcance |
|---|---|---|
| `roles/dataflow.worker` | proyecto | No existe versión por recurso; los workers lo necesitan para reportar su estado |
| `roles/bigquery.jobUser` | proyecto | Crear jobs de BigQuery solo se puede otorgar a nivel de proyecto |
| `roles/pubsub.subscriber` | subscription `reviews-dataflow-sub` | Consumir y confirmar mensajes |
| `roles/pubsub.viewer` | subscription `reviews-dataflow-sub` | Leer la configuración de la subscription; `subscriber` no lo incluye |
| `roles/storage.objectAdmin` | bucket `<proyecto>-data` | Escribir archivos en `raw/`, `dead-letter/`, `tmp/` y `staging/` |
| `roles/bigquery.dataEditor` | dataset `bronze` | Escribir filas en `bronze.reviews_raw`, y en ningún otro dataset |

### ADC y quota project

- **ADC (Application Default Credentials):** las credenciales que usan las librerías de Google (Python, Terraform) cuando no se les pasa ninguna. Se crean con `gcloud auth application-default login` y son distintas de las de `gcloud auth login`.
- **Quota project:** el proyecto al que se atribuyen las llamadas a las APIs hechas con esas credenciales. Si el ADC tiene configurado otro proyecto donde la API no está habilitada, la llamada falla con 403, aunque el recurso sea de este proyecto. `scripts/run_dataflow.sh` lo corrige solo para su proceso con la variable `GOOGLE_CLOUD_QUOTA_PROJECT`.

---

## Recorrido de un evento

```
 ① generator/publish.py
    arma el JSON → bytes UTF-8 → publish() al topic "reviews"
    ✗ falla la publicación → la excepción detiene el generador; el evento no existe todavía
          │
          ▼
 ② Pub/Sub topic "reviews"
    asigna message_id y publish_time, confirma al productor
          │  (una copia por subscription)
          ▼
 ③ subscription "reviews-dataflow-sub"
    guarda el mensaje hasta 7 días o hasta el ack
    ✗ Dataflow apagado → el mensaje espera; no se pierde
          │  Dataflow lo lee
          ▼
 ④ ParseAndValidate  (worker de Dataflow)
    publish_time → ingest_ts; validate() decide
    ✗ excepción de código → el elemento se reintenta sin fin, el job sigue "Running",
      el mensaje no se confirma y queda en Pub/Sub
          │
     ┌────┴────────────────────────┐
  válido                        rechazado (motivo único)
     │                              │
     ├─► ⑤ to_bq_row → Storage Write API → bronze.reviews_raw   (segundos)
     │      ✗ error de permisos o de schema → reintentos, el mensaje no se confirma
     │
     └─► ⑥ ventana de 5 min → GroupByKey → 1 archivo
            raw/reviews/dt=YYYY-MM-DD/reviews-HHMM-p0.jsonl      (al cerrar la ventana)
                                    │
                                    └─► ⑦ ventana de 5 min → GroupByKey → 1 archivo
                                           dead-letter/dt=YYYY-MM-DD/rejected-HHMM-p0.jsonl
          │
          ▼
 ⑧ Dataflow confirma (ack) el mensaje a Pub/Sub solo cuando el paso quedó guardado.
    ✗ un worker se cae antes del ack → Pub/Sub reentrega → duplicado con el mismo message_id
```

Latencia típica: segundos hasta bronze; hasta 5 minutos más el retraso del watermark hasta los archivos de GCS.
