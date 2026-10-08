"""Ask the agent from the terminal, with your own credentials, without deploying anything.

  echo "¿Por qué bajó la calificación de los audífonos esta semana?" | python agent/ask_local.py --project review-pulse-dev

The question comes from stdin because Windows consoles garble accents in command-line arguments.
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from google import genai  # noqa: E402
from google.cloud import bigquery  # noqa: E402

import agent  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--project", required=True)
p.add_argument("--today", help="pretend today is this ISO date (the sample data is from a fixed day)")
args = p.parse_args()
question = sys.stdin.buffer.read().decode("utf-8").strip()

# ADC may carry another project as quota project; scope the override to this process only.
os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = args.project
out = agent.ask(
    question,
    bq=bigquery.Client(project=args.project),
    client=genai.Client(vertexai=True, project=args.project, location="us-central1"),
    project=args.project,
    today=date.fromisoformat(args.today) if args.today else None,
)
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
