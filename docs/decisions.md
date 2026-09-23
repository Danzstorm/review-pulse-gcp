# Decisiones de diseño

ADRs cortos: decisión, contexto, por qué y alternativas descartadas. Un bloque por fase.

---

## Fase 1 — Infraestructura base (`infra/`)

### Un bucket con prefijos, no tres

**Decisión:** un solo `google_storage_bucket` con prefijos lógicos `raw/reviews/`, `dead-letter/` y `seed/`.
**Por qué:** ninguno de los tres necesita una política de retención o de acceso distinta. Separarlos en tres buckets sería el mismo comportamiento con tres nombres: estado extra sin beneficio.
**Alternativa descartada:** un bucket por propósito. Se reconsidera solo si alguno necesita una regla de ciclo de vida propia (por ejemplo, borrar `dead-letter/` a los 30 días).

### `uniform_bucket_level_access` + `force_destroy`

**Decisión:** bucket con `uniform_bucket_level_access = true` y `force_destroy = true`.
**Por qué:** el acceso uniforme obliga a que todo el control de acceso pase por IAM, sin ACLs legacy por objeto, así que hay un solo modelo de permisos para auditar. `force_destroy` hace falta porque Terraform no destruye por defecto un bucket con objetos, y el criterio de "terminado" exige que `terraform destroy` deje el proyecto limpio.
**Trade-off:** `force_destroy` es peligroso con datos de producción, porque borra el bucket con su contenido sin pedir confirmación. Aquí es aceptable: el bucket solo guarda datos sintéticos que se pueden regenerar.

### APIs habilitadas con `for_each`, no `count`

**Decisión:** `google_project_service` con `for_each = toset(local.fase1_apis)`.
**Por qué:** con `count`, cada API se identifica por índice numérico, y reordenar la lista hace que Terraform crea que se borró un recurso y se creó otro. Con `for_each` sobre un set, cada API tiene una clave estable (el nombre del servicio) y el orden de la lista no afecta el state.
**Alcance:** solo Pub/Sub, BigQuery, Storage y Dataflow. Vertex AI (Fase 3) y Cloud Run (Fase 4) se habilitan en su propia fase.

### `depends_on` explícito en storage, pubsub y bigquery, pero no en iam

**Decisión:** el bucket, el topic y los datasets llevan `depends_on = [google_project_service.fase1]`. Los bindings de IAM no lo necesitan.
**Por qué:** Terraform deduce el orden solo cuando un recurso referencia un atributo de otro. Por ejemplo, el binding de IAM que usa `google_bigquery_dataset.bronze.dataset_id` ya obliga a crear el dataset primero. Habilitar una API no produce ningún atributo que otro recurso cite, así que no hay una referencia que fije el orden. Sin `depends_on`, Terraform podría intentar crear el bucket antes de que la API de Storage esté habilitada, y GCP rechazaría la llamada.

### Datasets en Terraform, tablas en `sql/`

**Decisión:** Terraform crea `bronze`, `silver` y `gold` vacíos, con `delete_contents_on_destroy = true`. Las tablas las crean los scripts SQL.
**Por qué:** separa la infraestructura (qué datasets existen) del esquema de datos (qué forma tienen las tablas). Cambiar una columna no debería requerir tocar Terraform.
**Consecuencia:** como las tablas no están en el state de Terraform, sin `delete_contents_on_destroy` el `terraform destroy` fallaría al encontrar un dataset con contenido.
**Excepción (Fase 2):** `bronze.reviews_raw` sí está en Terraform. Ver "La tabla de aterrizaje vive en Terraform".

### Subscription sin expiración

**Decisión:** `expiration_policy { ttl = "" }` en la subscription de Dataflow.
**Por qué:** por defecto, Pub/Sub borra una subscription tras 31 días sin actividad. Este proyecto pasa semanas sin correr entre una demo y otra. Sin este ajuste, la subscription desaparecería, el pipeline no arrancaría y Terraform detectaría diferencias con el state.

### Alerta de presupuesto opcional

**Decisión:** `google_billing_budget` con umbrales de 50%, 90% y 100%, que solo se crea si se define `billing_account`.
**Por qué:** la sección 7 pide una alerta de presupuesto, y tenerla en Terraform la hace reproducible. Es opcional porque requiere permisos sobre la cuenta de facturación, y quien clone el repo quizá no los tenga. Sin esos permisos, `apply` debe funcionar igual.
**Detalles:** la API de Budgets rechaza llamadas con credenciales de usuario (ADC) si no tienen un quota project. Para resolverlo se usa un provider con alias (`google.billing`) que tiene `user_project_override`, así ese ajuste afecta solo al presupuesto. El monto no fija moneda porque la API exige que coincida con la moneda de la cuenta de facturación.

### IAM: `_iam_member` aditivo, no `_iam_binding` autoritativo

**Decisión:** todos los bindings usan la familia `google_*_iam_member`.
**Por qué:** `_iam_member` agrega un miembro a un rol sin tocar a los que ya lo tienen. `_iam_binding` es autoritativo: reemplaza la lista completa de miembros del rol y borraría sin aviso los permisos otorgados fuera de Terraform.

### Dos roles a nivel de proyecto sin alternativa por recurso

**Decisión:** la service account `dataflow-runner` tiene `roles/dataflow.worker` y `roles/bigquery.jobUser` a nivel de proyecto. Todos sus demás permisos están limitados al recurso: el dataset `bronze`, el bucket y la subscription.
**Por qué:** ninguno de los dos roles se puede otorgar sobre un recurso individual. Dataflow necesita `dataflow.worker` en el proyecto para que los workers reporten su estado. Para escribir en BigQuery con la Storage Write API hay que crear jobs, y eso exige `bigquery.jobUser` en el proyecto: no existe un permiso para crear jobs contra un solo dataset. Es una limitación de GCP, no una decisión de diseño.

### Sin backend remoto de state

**Decisión:** state local, sin un bucket de GCS como backend.
**Por qué:** lo mantiene una sola persona y no hay aplicaciones concurrentes que requieran state compartido con locking.
**Revisar si:** más de una persona empieza a aplicar cambios o Terraform se ejecuta desde CI.

---

## Fase 1 — Generador de eventos (`generator/`)

### Un solo catálogo para el generador y para `silver.products`

**Decisión:** `generator/products.csv` es la fuente de los `product_id` que publica el generador y también el archivo que se sube a `seed/` para crear `silver.products`.
**Por qué:** con dos listas separadas, tarde o temprano aparecen reseñas de productos que no existen en el catálogo, y el join con `products` las perdería sin avisar.

### El generador no envía el topic ni el sentimiento

**Decisión:** el evento solo trae `rating`, `title` y `body`. El topic y el sentimiento se usan internamente para elegir la plantilla, pero no se publican.
**Por qué:** en un sistema real el cliente no clasifica su propia reseña. Si el evento trajera la respuesta, el enriquecimiento con Gemini no tendría nada que demostrar.

### Inválidos que rompen exactamente una regla

**Decisión:** cada evento inválido rompe una sola regla: JSON mal formado (un mensaje cortado a mitad de camino), rating fuera de rango, un campo obligatorio ausente o un timestamp que no se puede parsear.
**Por qué:** así el motivo que registra la dead-letter es inequívoco y se puede verificar que cada tipo de error se detecta. El JSON mal formado es necesario porque el pipeline tiene dos puntos donde un evento puede fallar, el parseo y la validación, y cada uno debe recibir datos que lo pongan a prueba.

### Eventos que llegan tarde

**Decisión:** un 5% de los eventos trae un `event_ts` de entre 30 minutos y 6 horas atrás.
**Por qué:** en un sistema real, el momento en que ocurre un evento y el momento en que se ingiere no coinciden. Un celular sin conexión, por ejemplo, envía su reseña horas después. Con estos eventos, la decisión de particionar bronze por `ingest_ts` y silver por `event_ts` tiene efectos visibles: una reseña tardía puede caer en la partición de ayer en silver aunque haya entrado hoy en bronze.

### Duplicados como copia exacta

**Decisión:** un duplicado es la misma reseña reenviada, con el mismo `review_id` y el mismo contenido.
**Por qué:** así se ve el reintento de un productor, que es la causa real de duplicados en Pub/Sub (entrega at-least-once). La deduplicación ocurre en silver con MERGE, no en el generador ni en Dataflow.

### Tiempo lógico para la ventana del incidente

**Decisión:** el minuto en que cae cada evento se calcula como `i / rate`, no con el reloj del sistema.
**Por qué:** la ventana del incidente queda determinista. Con `--seed`, la misma ejecución produce siempre los mismos eventos, lo que permite testear el escenario sin esperar minutos reales.

### Modo `--dry-run`

**Decisión:** `--dry-run` escribe los eventos en stdout en lugar de publicarlos, y el SDK de Pub/Sub se importa solo cuando se publica de verdad.
**Por qué:** permite revisar y testear el generador sin un proyecto de GCP ni credenciales. Los bytes se escriben directo a `stdout.buffer` para que la consola de Windows no vuelva a codificar el UTF-8.

### Publicación síncrona

**Decisión:** cada `publish()` espera su confirmación antes de enviar el siguiente evento.
**Por qué:** a la tasa de la demo (unos 20 eventos por minuto) la latencia no importa, y un error aparece en el evento exacto que lo causó.
**Revisar si:** la tasa sube a miles de eventos por minuto. En ese caso conviene acumular los futures y esperarlos por lotes.

### Banco de reseñas con Gemini, pendiente para la Fase 3

**Decisión:** por ahora el texto sale de plantillas fijas. El banco de ~500 reseñas generado con Gemini que pide el spec queda pendiente.
**Por qué:** para generarlo hace falta la API de Vertex AI, que se habilita en la Fase 3. Las plantillas alcanzan para validar el pipeline de streaming. La variedad de texto solo importa cuando entran en juego los embeddings y la búsqueda semántica.

---

## Fase 2 — Pipeline de streaming (`pipelines/dataflow/`)

### Dataflow en lugar de una BigQuery subscription

**Decisión:** un pipeline de Beam en Dataflow lee de Pub/Sub y escribe en BigQuery y GCS.
**Alternativa descartada:** Pub/Sub ofrece *BigQuery subscriptions*, que escriben directo en una tabla sin código ni workers, y son más baratas y simples.
**Por qué no:** no permiten reglas de validación propias, no envían los rechazos a una dead-letter con motivo y no escriben la copia cruda en GCS para reprocesar. Si el requisito fuera solo "llevar mensajes a BigQuery", la subscription sería la opción correcta.

### Validación como función pura

**Decisión:** `validate(data, message_id, ingest_ts)` no usa Beam ni hace I/O. Devuelve `("valid", fila)` o `("invalid", rechazo)`. El DoFn solo la envuelve.
**Por qué:** la lógica que más cambia se testea en milisegundos, sin levantar un runner. Un test de contrato pasa miles de eventos del generador real por esta función y verifica que cada uno termine donde debe y por el motivo correcto.

### Un motivo por rechazo

**Decisión:** los rechazos tienen un único `reason`: `malformed_json`, `not_an_object`, `missing_fields:<campos>`, `invalid_rating`, `invalid_type` o `invalid_event_ts`. Las reglas se evalúan en ese orden y la primera que falla decide el motivo.
**Por qué:** la dead-letter se puede consultar por causa, y un aumento de un motivo concreto señala un problema concreto en el productor.
**Detalles:** en Python `True` es un `int`, así que el rating rechaza explícitamente los booleanos. Un timestamp sin zona horaria se rechaza porque es ambiguo.

### `ingest_ts` es el publish time de Pub/Sub

**Decisión:** `ingest_ts` toma el `publish_time` del mensaje, no la hora del reloj del worker.
**Por qué:** si Pub/Sub reenvía un mensaje, conserva el mismo `ingest_ts`, así que la fila duplicada cae en la misma partición y el MERGE de silver la resuelve sin casos especiales.

### Se guarda el `message_id`

**Decisión:** bronze guarda el `message_id` de Pub/Sub junto a cada fila.
**Por qué:** distingue los dos orígenes de duplicados. El mismo `review_id` con distinto `message_id` es un reintento del productor. El mismo `message_id` repetido es una reentrega de Pub/Sub.

### Storage Write API en modo at-least-once

**Decisión:** `WriteToBigQuery(method=STORAGE_WRITE_API, use_at_least_once=True)`.
**Por qué:** el modo exactly-once agrega un shuffle con costo y latencia, y protege contra algo que silver ya resuelve: la deduplicación vive en el MERGE. Pagar dos veces por la misma garantía no tiene sentido.
**Detalles verificados en el código de Beam 2.76:**
- En Python no existe `Method.STORAGE_API_AT_LEAST_ONCE`. Ese nombre aparece en la documentación de Java, y en Python se usa el flag `use_at_least_once`.
- La Storage Write API es un transform cross-language de Java: para construir el pipeline hace falta un JRE. La imagen de la Flex Template tiene que incluirlo.
- El schema es obligatorio aunque la tabla ya exista, y Beam acepta `INTEGER` pero no `INT64`.

### La tabla de aterrizaje vive en Terraform

**Decisión:** `bronze.reviews_raw` se crea en Terraform con `deletion_protection = false`, particionada por día sobre `ingest_ts` y con `require_partition_filter = true`. El schema está en `pipelines/dataflow/schemas/bronze_reviews_raw.json`, y lo leen tanto Terraform como el pipeline.
**Por qué:** el job no arranca sin la tabla, y `terraform apply` debe alcanzar para que el stack quede listo para correr. Las tablas derivadas (silver y gold) siguen en `sql/`. Con un solo archivo de schema, Terraform y el pipeline no pueden quedar desalineados.
**Detalles:** el provider crea las tablas con `deletion_protection = true` por defecto, lo que bloquearía el `destroy`. El filtro de partición obligatorio evita escaneos completos por accidente.

### Copia cruda en ventanas de 5 minutos, con un escritor propio

**Decisión:** los eventos válidos y los rechazados se escriben como JSON Lines en `raw/reviews/dt=YYYY-MM-DD/` y `dead-letter/dt=YYYY-MM-DD/`, con un archivo por ventana fija de 5 minutos y por disparo. El flujo es `WindowInto` → `GroupByKey` → un DoFn que escribe el grupo completo con `FileSystems.create`. El nombre depende solo de la ventana y del *pane*: `reviews-HHMM-p0.jsonl`.
**Por qué:** es la copia inmutable que permite reprocesar desde cero si cambia la lógica, y el prefijo `dt=` deja los archivos listos para una tabla externa particionada.
**Alternativa descartada (probada en la nube):** `fileio.WriteToFiles`. En streaming tuvo tres problemas:
1. Escribió un archivo por bundle: 281 archivos para 305 eventos.
2. Numera los shards desde 0 en cada grupo de archivos que mueve. Si una ventana genera dos grupos, los dos producen el mismo nombre y uno sobrescribe al otro. La dead-letter perdió así 8 de 10 rechazos, sin ningún error.
3. Si el rename desde `.temp` falla, solo lo registra en nivel DEBUG.

La conciliación detectó la pérdida: se esperaban 10 rechazos y había 2.
**Trade-off:** una sola clave hace que cada ventana pase por un único worker. A este volumen no importa. Si el volumen crece, hay que repartir la clave en N valores y agregar el número de shard al nombre del archivo.

### Contadores por resultado de validación

**Decisión:** `ParseAndValidate` incrementa un contador de Beam por resultado (`valid`, `malformed_json`, `invalid_rating`, etc.). Se ven en la consola de Dataflow y en Cloud Monitoring.
**Por qué:** así se puede conciliar lo que el pipeline clasificó con lo que llegó a cada destino. Con estos contadores, la pérdida en la dead-letter habría sido visible en la consola sin reproducir nada: el contador marcaba 10 rechazos y en GCS había 2.

### Sin prueba end-to-end local

**Decisión:** la lógica se prueba con tests unitarios y con `TestPipeline`, y la integración se prueba directamente en Dataflow.
**Por qué:** ningún runner local ejecuta este pipeline completo. El DirectRunner de Python no soporta transforms cross-language en streaming, y PrismRunner no soporta la lectura nativa de Pub/Sub. Agregar una opción para usar otro método de escritura en local significaría probar un camino de código que no corre en producción.

### El lanzamiento no espera al job

**Decisión:** con Dataflow, `run()` envía el job y devuelve el control. Solo con runners locales espera a que termine.
**Por qué:** un job de streaming nunca termina, así que `with beam.Pipeline()` dejaría colgado para siempre el comando de lanzamiento, y también el de la Flex Template.

### Quota project acotado al proceso

**Decisión:** `scripts/run_dataflow.sh` define `GOOGLE_CLOUD_QUOTA_PROJECT` con el proyecto de Terraform.
**Por qué:** las credenciales ADC de un usuario pueden tener otro proyecto como quota project. Entonces las llamadas se atribuyen a ese proyecto, y fallan con 403 si ahí la API no está habilitada. Cambiarlo con `gcloud auth application-default set-quota-project` afectaría a todo lo demás que use esas credenciales. La variable de entorno lo resuelve solo para este proceso.

### Todo lo que se enciende tiene cómo apagarse

**Decisión:** `scripts/stop.sh` hace drain de los jobs activos y espera a que se detengan. `scripts/down.sh` los cancela y después ejecuta `terraform destroy`.
**Por qué:** Terraform no administra el job de Dataflow, así que un `destroy` solo dejaría el job corriendo y cobrando. El drain termina de procesar lo que está en vuelo y conviene para cerrar una sesión. El cancel detiene todo de inmediato y conviene antes de destruir la infraestructura.

### Workers `e2-standard-2`

**Decisión:** `--worker_machine_type=e2-standard-2`, en lugar del tipo por defecto de Dataflow (familia n1).
**Por qué:** el primer lanzamiento falló con `ZONE_RESOURCE_POOL_EXHAUSTED`: `us-central1-a` no tenía capacidad para el tipo pedido, Dataflow reintentó 5 minutos y marcó el job como fallido. La familia e2 suele tener mejor disponibilidad y además es más barata. Para un worker que procesa decenas de eventos por minuto no hace falta más.

### `pubsub.viewer` sobre la subscription

**Decisión:** la service account de Dataflow tiene `roles/pubsub.viewer` sobre la subscription, además de `roles/pubsub.subscriber`.
**Por qué:** `subscriber` permite consumir mensajes, pero no incluye `pubsub.subscriptions.get`. Dataflow lee la configuración de la subscription al arrancar para validar el ack deadline y detectar opciones que no soporta. Sin ese permiso, el job igual arranca pero lo registra como advertencia. El permiso se otorga sobre la subscription y no sobre el proyecto, para mantener el mínimo privilegio.

### Los tests usan los tipos reales de producción

**Decisión:** el test del DoFn arma el `PubsubMessage` con `publish_time` de tipo `DatetimeWithNanoseconds`, que es lo que Dataflow entrega realmente.
**Por qué:** la primera versión usaba un `Timestamp` de Beam. El test pasó, pero en Dataflow cada evento falló con `'DatetimeWithNanoseconds' object has no attribute 'to_utc_datetime'`. Un test cuyos datos no tienen la forma de los de producción da una confianza falsa. Además, en streaming Dataflow reintenta indefinidamente los elementos que fallan: el job sigue en verde (`Running`) sin escribir nada, y los mensajes se quedan en Pub/Sub porque nunca se confirman. No se pierden datos, pero un job "verde" no garantiza que esté funcionando.
**Segundo caso:** el paso `Convert dict to Beam Row` de la Storage Write API no convierte `datetime` a `Timestamp` de Beam, y falló en Dataflow con `'datetime.datetime' object has no attribute 'micros'`. Ahora `to_bq_row` hace esa conversión justo antes de escribir, y hay un test que ejecuta en local ese mismo transform (`StorageWriteToBigQuery.ConvertToBeamRows`) sin tocar BigQuery. La regla que dejan los dos casos: cuando algo falla en la nube, primero se reproduce en local con el mismo transform, y recién después se corrige.

### Retrospectiva: qué se haría distinto

- **Primero un esqueleto que camine.** El pipeline se escribió completo antes de desplegarlo, así que en la nube aparecieron cinco problemas encadenados: quota project, stockout, un permiso faltante y dos errores de tipos. Un pipeline mínimo (Pub/Sub → BigQuery) desplegado al principio habría expuesto los problemas de infraestructura con mucho menos código en juego.
- **A este volumen, ELT sería más simple.** Una BigQuery subscription guarda todo crudo en bronze, incluidos los mensajes inválidos, y la validación se hace en SQL, con una tabla de cuarentena donde cada rechazo lleva su motivo. No hay workers, Java ni costo por hora. Dataflow se justifica con transformaciones por evento con estado, enriquecimientos complejos o mucho volumen; aquí se usa porque el objetivo del proyecto incluye dominarlo.
- **Validar al publicar.** Un schema de Pub/Sub (Avro o Protobuf) asociado al topic rechaza en el momento los mensajes mal formados, y en producción es la primera barrera. Aquí se omite a propósito, para que los errores lleguen a la dead-letter y se pueda demostrar ese camino.
- **Observabilidad desde el primer día.** Con los contadores por resultado desde el primer despliegue, la pérdida en la dead-letter habría sido visible en la consola sin tener que investigarla.

### Experimento: BigQuery subscription en paralelo

**Decisión:** mantener, junto al pipeline de Dataflow, una segunda subscription (`reviews-bigquery-sub`) que escribe cada mensaje crudo en `bronze.reviews_raw_bqsub`, con una vista SQL (`bronze.reviews_bqsub_classified`) que aplica las mismas reglas de validación. Todo el experimento vive en `infra/experiment_bq_subscription.tf`.
**Por qué:** para medir con los mismos mensajes el camino ELT, que la retrospectiva señaló como más simple a este volumen, en lugar de solo argumentarlo.
**Resultado (semilla 47, 300 eventos):** las cuatro mediciones coinciden (289 válidos y 11 rechazos, iguales por motivo). Al terminar de publicar, la subscription ya tenía las 300 filas; Dataflow todavía estaba consiguiendo una VM, tras un stockout. El detalle está en la guía, capítulo 05.
**Lo que se aprendió:**
- A este volumen, ELT tiene la misma exactitud con mucho menos código, sin costo por hora y sin arranque en frío.
- Su punto débil es la **paridad de reglas.** `SAFE_CAST` acepta timestamps sin zona horaria y la regla en Python no; sin una expresión regular extra, los dos caminos habrían diferido en silencio. Duplicar la lógica de validación exige una prueba explícita de paridad.
- **Mínimo privilegio:** el service agent de Pub/Sub tiene `dataEditor` solo sobre la tabla del experimento, no sobre el dataset.
- **`data` es STRING y no JSON**, para que los mensajes mal formados también aterricen y los clasifique SQL, en lugar de que BigQuery los rechace.

### Pendiente para la Fase 3: la carrera del watermark

El MERGE de silver va a leer las filas de bronze con `ingest_ts` mayor que el último watermark procesado. Pero `ingest_ts` es el publish time, y la fila recién se puede consultar unos segundos después, cuando la Storage Write API la confirma. Si el MERGE corre justo en ese intervalo, avanza el watermark y la fila queda atrás para siempre. La solución es releer con solapamiento (`ingest_ts > watermark - 15 minutos`), lo que es seguro porque el MERGE es idempotente.
