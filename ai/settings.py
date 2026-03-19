"""Centralized runtime settings loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


PG_DSN = os.getenv(
    "PG_DSN",
    "dbname=postgres user=postgres password=postgres host=192.168.17.128 port=5432",
)

INFLUX_URL = os.getenv("INFLUX_URL", "http://192.168.17.128:8086")
INFLUX_TOKEN = os.getenv(
    "INFLUX_TOKEN",
    "x1okc3hgLOH7AzkTfgw4-bGl4lOVdmTanTRsccguA5kZPK20X0guSekAfiF83bMYdaavTunqtaDjD8hs7txLwA==",
)
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "queue_data")
QUEUE_MEASUREMENT = os.getenv("QUEUE_MEASUREMENT", "factory")
QUEUE_FIELD = os.getenv("QUEUE_FIELD", "count")
QUEUE_TAG_KEY = os.getenv("QUEUE_TAG_KEY", "thingId")

LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.0-flash-free"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    "",
)
# AIHUBMIX_API_KEY = os.getenv("AIHUBMIX_API_KEY", os.getenv("OPENAI_API_KEY", "sk-pPu8NXQEM6v3eoVz8aB8Be7588784058Ac28D7A3E30f227b"))
AIHUBMIX_API_KEY = "sk-pPu8NXQEM6v3eoVz8aB8Be7588784058Ac28D7A3E30f227b"
AIHUBMIX_BASE_URL = os.getenv("AIHUBMIX_BASE_URL", "https://aihubmix.com/v1")

BRANCH_FILES_DIR = Path(os.getenv("BRANCH_FILES_DIR", "simulation/logistics/files"))
DITTO_HTTP_BASE = os.getenv("DITTO_HTTP_BASE", "http://192.168.17.128:8080")
DITTO_USER = os.getenv("DITTO_USER", "ditto")
DITTO_PASS = os.getenv("DITTO_PASS", "ditto")
