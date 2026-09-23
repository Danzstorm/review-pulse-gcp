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
