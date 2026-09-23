# Guía paso a paso

Cómo funciona Review Pulse por dentro y cómo reconstruirlo a mano, sin Terraform. Cubre lo construido hasta ahora: infraestructura base, generador de eventos y pipeline de streaming (fases 1 y 2).

| Capítulo | Contenido |
|---|---|
| [01 · Conceptos](01-conceptos.md) | Pub/Sub, Beam/Dataflow, BigQuery e IAM, explicados sobre este sistema. Recorrido de un evento de punta a punta. |
| [02 · Despliegue manual](02-despliegue-manual.md) | Cada recurso de `infra/` creado con `gcloud` y `bq`, en orden, con su verificación y su equivalente en Terraform. |
| [03 · Recorrido del código](03-recorrido-del-codigo.md) | `generator/publish.py` y `pipelines/dataflow/pipeline.py` función por función, y qué protege cada test. |
| [04 · Operar y validar](04-operar-y-validar.md) | Una sesión completa: tests, publicación, lanzamiento, monitoreo, conciliación, apagado y resolución de problemas. |

Orden sugerido: 01 → 02 → 03 → 04. El capítulo 02 y `terraform apply` producen el mismo resultado; basta con usar uno de los dos.

Las razones de cada decisión, con las alternativas descartadas, están en [`../decisions.md`](../decisions.md).
