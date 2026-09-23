# 03 · Recorrido del código

Dos programas y sus tests. Para cada pieza: qué hace, por qué tiene esa forma y qué se rompe si se quita.

---

## `generator/publish.py`

Publica reseñas sintéticas en Pub/Sub. Su objetivo no es solo producir volumen: produce **exactamente los casos que el pipeline tiene que saber manejar**, y de forma reproducible.

### Constantes y `TEMPLATES`

`TEMPLATES` guarda pares `(título, cuerpo)` por tema (`bateria`, `conectividad`, `envio`, `precio`, `calidad`) y sentimiento (`pos`, `neg`). El generador elige una plantilla según el tema y el sentimiento, **pero no publica ninguno de los dos**: el evento solo lleva `rating`, `title` y `body`. Así Gemini, en la Fase 3, tiene que inferirlos desde el texto, como pasaría con reseñas reales.

`FIELDS` es el conjunto de campos de un evento válido y lo usan los tests.

### `make_event(rng, product_ids, ...)`

Arma una reseña válida. Puntos clave:

```python
"review_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
"rating": rng.randint(4, 5) if sentiment == "pos" else rng.randint(1, 2),
```

- El `review_id` sale del generador aleatorio con semilla (`rng`), no de `uuid.uuid4()`. Con la misma `--seed`, los mismos IDs. Sin esto, la conciliación del capítulo 04 no sería posible.
- El rating es coherente con el sentimiento de la plantilla.

### `corrupt(event, rng)`

Rompe **exactamente una** regla, para que el motivo de rechazo sea inequívoco:

| Regla rota | Cómo | Motivo esperado en el pipeline |
|---|---|---|
| `malformed` | corta el JSON 12 caracteres antes del final (queda dentro de `event_ts`, la última clave) | `malformed_json` |
| `rating` | pone 0, 6, 10 o -1 | `invalid_rating` |
| `missing_field` | borra `review_id`, `product_id` o `body` | `missing_fields:<campo>` |
| `timestamp` | pone `"yesterday"` | `invalid_event_ts` |

El caso `malformed` devuelve un `str` en lugar de un `dict`, porque ya no es un objeto JSON.

### `next_event(rng, product_ids, recent, elapsed_min, args)`

Decide qué se publica en cada paso, en este orden:

1. **Duplicado** (`--dup-ratio`, 2%): devuelve una copia exacta de un evento reciente. Mismo `review_id`: simula un reintento del productor.
2. **Incidente** (`--incident-start`, `--incident-minutes`): dentro de la ventana, la mitad de los eventos son reseñas negativas de conectividad para `--incident-product` (P-0001).
3. **Evento tardío** (`--late-ratio`, 5%): cambia `event_ts` a entre 30 minutos y 6 horas atrás.
4. **Inválido** (`--invalid-ratio`, 4%): pasa el evento por `corrupt()`.

`elapsed_min` se calcula como `i / rate` (tiempo lógico), no con el reloj. Por eso la ventana del incidente es determinista y los tests la verifican en milisegundos.

### `encode(event)` y `main()`

`encode` convierte un evento en bytes UTF-8; si ya es texto (el caso malformado), lo usa tal cual.

`main` tiene dos modos:
- `--dry-run`: escribe cada evento en `stdout.buffer`. No necesita GCP ni el SDK de Pub/Sub, que se importa solo en el otro modo. Se escribe a `stdout.buffer` y no con `print` porque la consola de Windows recodificaría el UTF-8.
- Normal: `client.publish(topic_path, data).result()` espera la confirmación de cada mensaje. Es síncrono a propósito: a 20–60 eventos por minuto la latencia no importa, y si algo falla, falla en el evento exacto.

---

## `pipelines/dataflow/pipeline.py`

### `validate(data, message_id, ingest_ts)`

El núcleo del pipeline, y **función pura**: no usa Beam ni hace I/O. Recibe los bytes del mensaje y devuelve `("valid", fila)` o `("invalid", rechazo)`.

Las reglas se evalúan en este orden, y la primera que falla decide el motivo:

| # | Regla | Motivo |
|---|---|---|
| 1 | Los bytes son UTF-8 y JSON válido | `malformed_json` |
| 2 | El JSON es un objeto, no una lista ni un número | `not_an_object` |
| 3 | Están los 8 campos obligatorios y no están vacíos | `missing_fields:<lista>` |
| 4 | `rating` es un entero entre 1 y 5, y no un booleano | `invalid_rating` |
| 5 | Los demás campos son texto | `invalid_type` |
| 6 | `event_ts` es una fecha ISO 8601 **con zona horaria** | `invalid_event_ts` |

El orden importa: no tiene sentido revisar el rating de algo que no es JSON. Dos detalles no evidentes:

```python
if isinstance(rating, bool) or not isinstance(rating, int) ...
```
En Python `True` es un `int` (vale 1), así que sin el chequeo explícito `"rating": true` pasaría como rating 1.

```python
if event_ts.tzinfo is None:
    return reject("invalid_event_ts")
```
Una fecha sin zona horaria es ambigua: no se sabe si es UTC u hora local. Se rechaza en lugar de adivinar.

La fila válida lleva `event_ts` convertido a UTC, más `ingest_ts` y `message_id`. El rechazo lleva el `payload` original (decodificado con `errors="replace"`, para que incluso bytes inválidos se puedan guardar), el motivo, el `message_id` y el `ingest_ts`.

**Si se quita la pureza** (por ejemplo, si `validate` escribiera directamente en GCS), la lógica que más cambia dejaría de poder testearse sin levantar un runner.

### `ParseAndValidate` (DoFn)

Adapta `validate` a Beam:

```python
published = message.publish_time
if published.tzinfo is None:
    published = published.replace(tzinfo=timezone.utc)
ingest_ts = datetime.fromtimestamp(published.timestamp(), tz=timezone.utc)
```

- `ingest_ts` es el `publish_time` de Pub/Sub, no la hora del worker: si Pub/Sub reentrega un mensaje, el duplicado conserva el mismo `ingest_ts` y cae en la misma partición.
- En Dataflow, `publish_time` llega como `DatetimeWithNanoseconds`, una subclase de `datetime`. Se normaliza a un `datetime` UTC común para que el resto del código trabaje con un solo tipo.

Después incrementa un contador por resultado (`Metrics.counter("validation", ...)`), visible en la consola de Dataflow, y emite el registro a la salida principal (válidos) o a la salida `"invalid"`:

```python
yield beam.pvalue.TaggedOutput("invalid", record)
```

### `to_bq_row(row)`

Convierte `event_ts` e `ingest_ts` de `datetime` a `Timestamp` de Beam, justo antes de escribir en BigQuery. El conector de la Storage Write API convierte cada dict en una fila de Beam, y el codificador de `TIMESTAMP` de esas filas solo acepta `Timestamp` de Beam. **Si se quita:** cada fila falla con `'datetime.datetime' object has no attribute 'micros'`.

La conversión se hace en el borde con BigQuery y no dentro de `validate`, porque la copia en JSON (`to_json_line`) necesita `datetime` comunes.

### `to_json_line(record)`

```python
json.dumps(record, ensure_ascii=False, default=lambda ts: ts.isoformat())
```
Serializa un registro como una línea JSON. `default` convierte los `datetime` a texto ISO, y `ensure_ascii=False` deja las tildes como caracteres y no como `í`.

### `window_file_name`, `WriteWindowFile` y `write_windowed_jsonl`

Escriben la copia cruda (`raw/reviews/`) y la dead-letter (`dead-letter/`) en archivos de 5 minutos.

```python
| WindowInto(FixedWindows(WINDOW_SECONDS))   # corta el flujo en tramos de 5 min
| WithKeys(0)                                # misma clave para todo
| GroupByKey()                               # un grupo por ventana, al cerrarse
| ParDo(WriteWindowFile(path, prefix))       # un archivo por grupo
```

- `window_file_name` arma `dt=YYYY-MM-DD/<prefijo>-HHMM-p<pane>.jsonl` a partir del inicio de la ventana y del número de pane. Es una función pura: la misma ventana y el mismo pane dan siempre el mismo nombre.
- `WriteWindowFile` recibe el grupo completo y lo escribe de una vez con `FileSystems.create`. Si Dataflow reintenta el paso, reescribe el mismo archivo con el mismo contenido: no duplica ni pisa a otro.
- La clave única (`WithKeys(0)`) hace que cada ventana pase por un solo worker. A este volumen no importa; con mucho volumen habría que repartir la clave en N valores y agregar el número de shard al nombre.

**Por qué no se usa `fileio.WriteToFiles`**, la opción estándar de Beam: se probó en Dataflow y en streaming escribió un archivo por bundle (281 archivos para 305 eventos). Además numeraba los archivos desde 0 en cada grupo, así que dos grupos de la misma ventana producían el mismo nombre y uno sobrescribía al otro. La dead-letter perdió así 8 de 10 rechazos sin ningún error visible.

### `ReviewOptions`

Declara las tres opciones propias del pipeline (`--input_subscription`, `--bronze_table`, `--bucket`). Beam las mezcla con sus opciones estándar (`--runner`, `--project`, `--region`, …), así todas se pasan por la línea de comandos de la misma forma. No se marcan como obligatorias en argparse porque Beam vuelve a leer las opciones en los workers; `run()` valida que estén presentes.

### `run(argv)`

Arma y lanza el grafo:

```python
results = (
    p
    | "ReadPubSub" >> beam.io.ReadFromPubSub(subscription=..., with_attributes=True)
    | "ParseValidate" >> beam.ParDo(ParseAndValidate()).with_outputs("invalid", main="valid")
)
```

- `with_attributes=True` hace que cada elemento sea un `PubsubMessage` completo (con `message_id` y `publish_time`), no solo los bytes.
- `results.valid` va a `to_bq_row` → `WriteToBigQuery`, y además a `write_windowed_jsonl` hacia `raw/`. `results.invalid` va a `write_windowed_jsonl` hacia `dead-letter/`.
- `WriteToBigQuery` usa `STORAGE_WRITE_API` con `use_at_least_once=True`, `CREATE_NEVER` (la tabla la crea la infraestructura) y el schema leído del mismo JSON que usa Terraform. El schema es obligatorio en Python aunque la tabla exista.

Al final:

```python
result = p.run()
if "Dataflow" not in str(options.view_as(StandardOptions).runner):
    result.wait_until_finish()
```
Con Dataflow, el script envía el job y termina. Un job de streaming nunca finaliza, así que esperar dejaría la terminal colgada para siempre.

`save_main_session = True` envía a los workers las importaciones y constantes del módulo; sin esto, las funciones que usan `json`, `datetime` o `REQUIRED_FIELDS` fallarían en el worker.

---

## Tests

Se ejecutan con Python directamente, sin pytest:

```bash
.venv/Scripts/python tests/test_generator.py
.venv/Scripts/python tests/test_pipeline.py
```

### `tests/test_generator.py`

| Test | Protege contra |
|---|---|
| `test_invalid_ratio_is_respected` | que la proporción de inválidos se aleje del 4% configurado |
| `test_duplicates_reuse_review_id` | que los duplicados dejen de reutilizar el `review_id` y la deduplicación de silver no tenga nada que resolver |
| `test_incident_concentrates_negative_connectivity_reviews` | que el escenario de incidente deje de concentrar reseñas negativas de P-0001 dentro de su ventana |
| `test_malformed_payloads_do_not_parse` | que el caso "JSON cortado" produzca por accidente un JSON válido |
| `test_late_events_lag_behind_ingest_time` | que los eventos tardíos dejen de tener un `event_ts` en el pasado |

### `tests/test_pipeline.py`

| Test | Protege contra |
|---|---|
| `test_contract_with_generator` | que el generador y el pipeline dejen de estar de acuerdo. Genera 3.000 eventos reales y verifica que cada uno termine con el resultado y el motivo esperados. |
| `test_valid_row_normalizes_timestamps_to_utc` | fechas con otra zona horaria guardadas sin convertir a UTC |
| `test_edge_cases_are_rejected_with_one_reason` | casos límite: bytes no UTF-8, JSON que no es objeto, rating `true` o `"5"`, título numérico, fecha sin zona horaria, cuerpo vacío |
| `test_parse_and_validate_routes_outputs_in_beam` | que el DoFn mande válidos y rechazados a la salida equivocada. **Nació de un fallo real:** usa `DatetimeWithNanoseconds` como `publish_time` porque es el tipo que entrega Dataflow; la primera versión usaba otro tipo, pasaba en verde y fallaba en producción. |
| `test_rows_survive_the_bigquery_conversion_step` | **Nació de un fallo real:** ejecuta en local el mismo paso que falló en Dataflow (`StorageWriteToBigQuery.ConvertToBeamRows`) sin tocar BigQuery. |
| `test_one_file_per_window_and_no_lost_lines` | **Nació de un fallo real:** 25 eventos en la misma ventana deben terminar en un solo archivo, sin perder ninguna línea. |
| `test_file_name_is_a_pure_function_of_window_and_pane` | que el nombre de archivo dependa de algo más que la ventana y el pane, lo que reabriría la posibilidad de colisiones |

Los tres tests marcados nacieron de fallos que ocurrieron en Dataflow. Cada uno reproduce en local el camino exacto que falló, para que ese error no vuelva a aparecer sin que un test lo detecte.
