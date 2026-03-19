import json
from typing import Any, Dict, Optional, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor

from ai.settings import PG_DSN

def pg_fetch_config(config_key: str) -> Dict[str, Any]:
    try:
        with psycopg2.connect(PG_DSN) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT content FROM jaamsim_config WHERE config_key = %s LIMIT 1", (config_key,))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"Config not found: config_key={config_key}")
                content = row["content"]
                if isinstance(content, (dict, list)):
                    return content
                return json.loads(content)
    except psycopg2.errors.UndefinedTable:
        raise RuntimeError("Table not found: jaamsim_config")

def pg_insert_incident(event: Dict[str, Any], facts: Dict[str, Any], trace: Any, analysis: str) -> int:
    thing_id = event.get("thingId")
    alert_type = event.get("alert_type")
    event_time = event.get("time")  # ISO string

    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                INSERT INTO incident_analysis(thing_id, alert_type, event_time, event, facts, trace, analysis)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                RETURNING id
                ''',
                (
                    thing_id,
                    alert_type,
                    event_time,
                    json.dumps(event, ensure_ascii=False),
                    json.dumps(facts, ensure_ascii=False),
                    json.dumps(trace, ensure_ascii=False),
                    analysis
                )
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    return int(new_id)

def pg_list_incidents(limit: int = 50):
    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT id, thing_id, alert_type, event_time, received_at
                FROM incident_analysis
                ORDER BY received_at DESC
                LIMIT %s
                ''',
                (limit,)
            )
            return cur.fetchall()

def pg_get_incident(incident_id: int) -> Optional[Dict[str, Any]]:
    with psycopg2.connect(PG_DSN) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                '''
                SELECT id, thing_id, alert_type, event_time, received_at, event, facts, trace, analysis
                FROM incident_analysis
                WHERE id = %s
                ''',
                (incident_id,)
            )
            return cur.fetchone()
