# Guía paso a paso

Cómo funciona Review Pulse por dentro y cómo reconstruirlo a mano, sin Terraform. Cubre lo construido hasta ahora: infraestructura base, generador de eventos, pipeline de streaming y, de la Fase 3, la capa silver.

| Capítulo | Contenido |
|---|---|
| [01 · Conceptos](01-conceptos.md) | Pub/Sub, Beam/Dataflow, BigQuery e IAM, explicados sobre este sistema. Recorrido de un evento de punta a punta. |
| [02 · Despliegue manual](02-despliegue-manual.md) | Cada recurso de `infra/` creado con `gcloud` y `bq`, en orden, con su verificación y su equivalente en Terraform. |
| [03 · Recorrido del código](03-recorrido-del-codigo.md) | `generator/publish.py` y `pipelines/dataflow/pipeline.py` función por función, y qué protege cada test. |
| [04 · Operar y validar](04-operar-y-validar.md) | Una sesión completa: tests, publicación, lanzamiento, monitoreo, conciliación, apagado y resolución de problemas. |
| [05 · Alternativas](05-alternativas.md) | Otros caminos para el streaming en GCP y un experimento medido: BigQuery subscription + validación en SQL, en paralelo con Dataflow. |
| [06 · Capas de datos](06-capas-de-datos.md) | Bronze, silver y gold: qué garantiza cada capa y cómo se construye, con diagramas. Crece con la Fase 3. |

Orden sugerido: 01 → 02 → 03 → 04 → 05. El capítulo 02 y `terraform apply` producen el mismo resultado; basta con usar uno de los dos.

Las razones de cada decisión, con las alternativas descartadas, están en [`../decisions.md`](../decisions.md).
