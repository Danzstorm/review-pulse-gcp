"""Publish synthetic product reviews to Pub/Sub.

Examples:
  python generator/publish.py --project my-proj --rate 20 --duration 10
  python generator/publish.py --project my-proj --incident-start 2 --incident-minutes 5
  python generator/publish.py --dry-run --rate 600 --duration 1 --seed 7
"""

import argparse
import csv
import json
import random
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRODUCTS_CSV = Path(__file__).with_name("products.csv")
FIELDS = {"review_id", "product_id", "customer_id", "rating", "title", "body", "channel", "event_ts"}
INCIDENT_SHARE = 0.5

# (title, body) pairs per topic and sentiment. Gemini infers the topic later;
# the generator never sends it, so the enrichment step has real work to do.
TEMPLATES = {
    "bateria": {
        "pos": [
            ("La batería dura muchísimo", "Lo uso todo el día y en la noche todavía le queda carga."),
            ("Carga rápida", "En media hora pasa del 10% al 80%, muy práctico para salir."),
            ("Autonomía excelente", "Llevo una semana sin cargarlo y sigue funcionando."),
        ],
        "neg": [
            ("La batería no dura nada", "Con uso normal se descarga en menos de tres horas."),
            ("Se calienta al cargar", "Cada vez que lo cargo se pone muy caliente y tarda horas."),
            ("Dejó de cargar", "A las dos semanas ya no toma carga aunque cambie el cable."),
        ],
    },
    "conectividad": {
        "pos": [
            ("Empareja al instante", "Se conecta con el teléfono en segundos y nunca se corta."),
            ("Buena señal", "Puedo alejarme varios metros del teléfono y sigue conectado."),
        ],
        "neg": [
            ("Se desconectan solos", "A los 20 minutos el audífono izquierdo pierde conexión y hay que emparejarlo de nuevo."),
            ("El Bluetooth falla", "Se corta la señal aunque el teléfono esté al lado."),
            ("No reconoce el teléfono", "Después de la última actualización ya no aparece en la lista de dispositivos."),
            ("Cortes constantes", "Cada pocos minutos se escucha entrecortado y luego se desconecta."),
        ],
    },
    "envio": {
        "pos": [
            ("Llegó antes de lo esperado", "Lo pedí el lunes y el martes ya estaba en la casa."),
            ("Bien empacado", "La caja llegó en perfecto estado y con todo protegido."),
        ],
        "neg": [
            ("El envío tardó semanas", "Me prometieron tres días y llegó casi un mes después."),
            ("Caja dañada", "El paquete llegó abierto y faltaba el cargador."),
        ],
    },
    "precio": {
        "pos": [
            ("Excelente relación calidad-precio", "Por lo que cuesta, rinde mucho más de lo que esperaba."),
            ("Buena oferta", "Lo compré con descuento y vale cada peso."),
        ],
        "neg": [
            ("Demasiado caro", "Hay opciones similares por la mitad del precio."),
            ("No vale lo que cuesta", "Para ese precio esperaba mejores materiales."),
        ],
    },
    "calidad": {
        "pos": [
            ("Muy bien construido", "Se siente sólido y los materiales son de buena calidad."),
            ("Funciona perfecto", "Hace exactamente lo que promete, sin fallas."),
        ],
        "neg": [
            ("Se rompió rápido", "A los dos meses se partió la bisagra con uso normal."),
            ("Materiales frágiles", "El plástico se ve barato y ya tiene rayones."),
        ],
    },
}


def utc_ts(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_product_ids(path=PRODUCTS_CSV):
    with open(path, encoding="utf-8") as f:
        return [row["product_id"] for row in csv.DictReader(f)]


def make_event(rng, product_ids, *, product_id=None, topic=None, sentiment=None):
    topic = topic or rng.choice(list(TEMPLATES))
    sentiment = sentiment or rng.choices(["pos", "neg"], weights=[7, 3])[0]
    title, body = rng.choice(TEMPLATES[topic][sentiment])
    return {
        "review_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "product_id": product_id or rng.choice(product_ids),
        "customer_id": f"C-{rng.randint(1, 5000):04d}",
        "rating": rng.randint(4, 5) if sentiment == "pos" else rng.randint(1, 2),
        "title": title,
        "body": body,
        "channel": rng.choice(["app", "web", "email"]),
        "event_ts": utc_ts(),
    }


def corrupt(event, rng):
    """Break exactly one rule, so the dead-letter reason is unambiguous.

    Returns a dict, or a str for the malformed case (a message cut off mid-JSON).
    """
    bad = dict(event)
    rule = rng.choice(["malformed", "rating", "missing_field", "timestamp"])
    if rule == "malformed":
        return json.dumps(bad, ensure_ascii=False)[:-12]  # cuts into event_ts, the last key
    if rule == "rating":
        bad["rating"] = rng.choice([0, 6, 10, -1])
    elif rule == "missing_field":
        del bad[rng.choice(["review_id", "product_id", "body"])]
    else:
        bad["event_ts"] = "yesterday"
    return bad


def next_event(rng, product_ids, recent, elapsed_min, args):
    if recent and rng.random() < args.dup_ratio:
        return dict(rng.choice(recent))  # same review_id: what a producer retry looks like

    in_incident = (
        args.incident_start is not None
        and args.incident_start <= elapsed_min < args.incident_start + args.incident_minutes
    )
    if in_incident and rng.random() < INCIDENT_SHARE:
        event = make_event(rng, product_ids, product_id=args.incident_product, topic="conectividad", sentiment="neg")
    else:
        event = make_event(rng, product_ids)

    # Late arrival: event time hours behind ingest time, like a phone that was offline.
    if rng.random() < args.late_ratio:
        event["event_ts"] = utc_ts(minutes_ago=rng.randint(30, 360))

    if rng.random() < args.invalid_ratio:
        return corrupt(event, rng)
    recent.append(event)
    return event


def encode(event):
    text = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    return text.encode("utf-8")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", help="GCP project id (required unless --dry-run)")
    p.add_argument("--topic", default="reviews")
    p.add_argument("--rate", type=float, default=20, help="events per minute")
    p.add_argument("--duration", type=float, default=10, help="minutes")
    p.add_argument("--invalid-ratio", type=float, default=0.04)
    p.add_argument("--dup-ratio", type=float, default=0.02)
    p.add_argument("--late-ratio", type=float, default=0.05)
    p.add_argument("--incident-product", default="P-0001")
    p.add_argument("--incident-start", type=float, help="minute the incident starts; omit for no incident")
    p.add_argument("--incident-minutes", type=float, default=5)
    p.add_argument("--seed", type=int)
    p.add_argument("--dry-run", action="store_true", help="print events instead of publishing")
    args = p.parse_args(argv)
    if not args.dry_run and not args.project:
        p.error("--project is required unless --dry-run")
    return args


def main(argv=None):
    args = parse_args(argv)
    rng = random.Random(args.seed)
    product_ids = load_product_ids()
    recent = deque(maxlen=50)

    if args.dry_run:
        send = lambda data: sys.stdout.buffer.write(data + b"\n")
    else:
        from google.cloud import pubsub_v1  # lazy: --dry-run works without the SDK

        client = pubsub_v1.PublisherClient()
        topic_path = client.topic_path(args.project, args.topic)
        # ponytail: synchronous publish, fine at demo rates (~20/min); batch futures if rate grows to thousands/min
        send = lambda data: client.publish(topic_path, data).result()

    total = int(args.rate * args.duration)
    for i in range(total):
        event = next_event(rng, product_ids, recent, elapsed_min=i / args.rate, args=args)
        send(encode(event))
        if not args.dry_run:
            time.sleep(60 / args.rate)


if __name__ == "__main__":
    main()
