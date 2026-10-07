# 06 · Capas de datos: bronze, silver y gold

Hasta la fase 2 los datos llegan a **bronze**, tal como salen del pipeline. Este capítulo cubre cómo se convierten en tablas confiables para consultar. Se completa con cada paso de la Fase 3.

## El mapa

```mermaid
flowchart LR
    subgraph B[bronze · llegó, sin garantías]
        BR[(reviews_raw<br/>at-least-once<br/>puede repetir review_id)]
    end
    subgraph S[silver · limpio y único]
        SR[(reviews<br/>1 fila por review_id)]
        SP[(products<br/>catálogo)]
    end
    subgraph G[gold · listo para responder]
        GE[(reviews_enriched<br/>sentimiento · tema · resumen)]
        GV[(review_embeddings)]
        GM[(product_daily_metrics)]
    end
    BR -->|MERGE sin duplicados| SR
    CSV[products.csv] -->|bq load| SP
    SR -->|Gemini| GE
    GE -.-> GV
    GE -.-> GM
    SP -.-> GM
```

Línea continua: ya construido. Línea punteada: pendiente.

| Capa | Pregunta que responde | Garantía |
|---|---|---|
| bronze | ¿Qué llegó y cuándo? | Ninguna: puede haber duplicados |
| silver | ¿Qué reseñas existen? | Un `review_id` aparece una sola vez |
| gold | ¿Qué significan? | Enriquecidas y agregadas para el agente |

## Silver: deduplicar con un MERGE

Pub/Sub entrega *al menos una vez*, y el generador reenvía el 2 % de los eventos a propósito. Por eso bronze tiene más filas que reseñas. La deduplicación ocurre una sola vez, en silver.

```mermaid
flowchart TB
    A[bronze.reviews_raw<br/>1.164 filas] -->|"WHERE ingest_ts en los últimos N días"| B[ventana de lectura]
    B -->|"ROW_NUMBER por review_id<br/>ORDER BY ingest_ts"| C[solo la primera copia<br/>1.132 filas]
    C --> D{¿existe el review_id<br/>en silver?}
    D -->|no| E[INSERT]
    D -->|sí| F[nada]
```

Por qué es seguro ejecutarlo las veces que haga falta:

- **Solo inserta.** Si la fila ya está, no hace nada. Correrlo dos veces, o con ventanas que se solapan, da el mismo resultado (idempotencia).
- **Ventana fija en vez de watermark.** Un watermark ("procesar lo posterior a la última corrida") puede saltarse filas que bronze aún no hacía consultables. Releer una ventana fija no puede perder nada, y la idempotencia hace que releer sea gratis en datos. La decisión completa está en [`decisions.md`](../decisions.md).

### Ejecutarlo

```bash
bash scripts/load_products.sh                              # silver.products desde generator/products.csv
LOOKBACK_DAYS=30 bash scripts/run_sql.sh sql/silver/reviews.sql   # crea silver.reviews y hace el MERGE
```

`LOOKBACK_DAYS` vale 3 por defecto. Se amplía para reprocesar historia, por ejemplo después de borrar silver.

### Comprobarlo

```sql
SELECT
  (SELECT COUNT(*) FROM silver.reviews)                    AS silver_rows,
  (SELECT COUNT(DISTINCT review_id) FROM silver.reviews)   AS silver_distinct,
  (SELECT COUNT(DISTINCT review_id) FROM bronze.reviews_raw
    WHERE ingest_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)) AS bronze_distinct;
```

`silver_rows`, `silver_distinct` y `bronze_distinct` deben ser iguales. Si `silver_rows` es mayor que `silver_distinct`, el MERGE está insertando duplicados.

### Particiones

```mermaid
flowchart LR
    subgraph bronze [bronze · particionada por ingest_ts]
        b1[hoy]
    end
    subgraph silver [silver · particionada por event_ts]
        s1[hoy]
        s2[hace 3 horas<br/>reseña tardía]
    end
    b1 -->|reseña a tiempo| s1
    b1 -->|reseña con retraso| s2
```

Bronze se ordena por *cuándo llegó* y silver por *cuándo ocurrió*. Una reseña que llega hoy con `event_ts` de hace horas queda en la partición de su fecha real. Las consultas por fecha de reseña leen solo lo necesario.

---

## Gold: enriquecer con Gemini

Las reseñas de silver tienen texto libre. Para responder "¿por qué bajó la calificación?" hacen falta tres datos estructurados por reseña: **sentimiento**, **tema** y **resumen**. Gemini los produce sin salir de BigQuery.

```mermaid
flowchart LR
    S[(silver.reviews)] -->|"solo las que faltan<br/>LEFT JOIN + LIMIT"| P[prompt por reseña]
    P -->|AI.GENERATE_TABLE| M[[modelo remoto<br/>gold.gemini]]
    M -->|"conexión BigQuery<br/>roles/aiplatform.user"| V{{Gemini en Vertex AI}}
    V --> M
    M --> F{¿respuesta válida?<br/>sentimiento y tema en la lista}
    F -->|sí| G[(gold.reviews_enriched)]
    F -->|no| R[no se guarda:<br/>se reintenta en la próxima corrida]
```

### Piezas

| Pieza | Dónde | Para qué |
|---|---|---|
| Conexión de BigQuery | `infra/enrichment.tf` | La identidad con la que BigQuery llama a Vertex AI |
| Rol `aiplatform.user` | `infra/enrichment.tf` | Permiso de esa identidad para usar Gemini |
| Modelo remoto `gold.gemini` | `sql/enrichment/model.sql` | Un nombre dentro de BigQuery que apunta al endpoint de Gemini |
| Consulta de enriquecimiento | `sql/enrichment/reviews_enriched.sql` | Arma el prompt, llama al modelo, valida y guarda |

```bash
terraform -chdir=infra apply                       # conexión + permiso
bash scripts/run_sql.sh sql/enrichment/model.sql   # modelo remoto
ENRICH_LIMIT=1200 bash scripts/run_sql.sh sql/enrichment/reviews_enriched.sql
```

`ENRICH_LIMIT` limita cuántas reseñas se envían en una corrida (200 por defecto). Como la consulta solo toma reseñas sin enriquecer, se puede correr varias veces hasta cubrirlas todas.

### Un prompt que obedece: el caso real

El tema debe salir de una lista cerrada (`battery`, `connectivity`, `sound_quality`, `comfort`, `build_quality`, `shipping`, `price`, `support`, `other`). Si no, un `GROUP BY topic` cuenta por separado `battery` y `battery life`.

| Intento | Cambio | Temas dentro de la lista |
|---|---|---|
| 1 | La lista al principio del prompt, temperatura por defecto | 15 % (173 de 1.132) |
| 2 | La instrucción **después** del texto de la reseña, `MUST … nothing else`, `temperature = 0`, `max_output_tokens = 200` | 100 % (30 de 30) |

Dos lecciones: el modelo pondera más lo último que lee, y un tope de tokens corta de raíz las salidas que degeneran en texto repetido. El `WHERE` final de la consulta es la red de seguridad: lo que no cumpla la lista no entra a gold.

### Comprobarlo

```sql
SELECT topic, COUNTIF(sentiment = 'positive') AS pos, COUNTIF(sentiment = 'negative') AS neg
FROM gold.reviews_enriched GROUP BY topic ORDER BY topic;   -- solo nueve valores

SELECT COUNT(*) FROM gold.reviews_enriched
WHERE (rating >= 4 AND sentiment = 'negative') OR (rating <= 2 AND sentiment = 'positive');  -- 0
```
