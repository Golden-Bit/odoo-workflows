#!/usr/bin/env python3
"""
End-to-end demo che usa la REST-API (`odoo_api.py`) invece dello SDK diretto.

Cosa fa
-------
1. POST /projects                     → crea progetto Scrum
2. POST /projects/{id}/stages         → crea 4 colonne
3. POST /tasks                        → crea task & sotto-task
4. PATCH /tasks/{id}/move             → sposta il task padre a *Done*
5. GET  /tasks?project_id=…           → riepilogo
6. Pulizia interattiva:
   • GET  /projects?name=…            → trova progetti omonimi
   • DELETE /tasks/{id}               → elimina i task
   • DELETE /stages/{id}              → elimina le colonne
   • DELETE /projects/{id}            → elimina il progetto
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pprint import pprint
from typing import Dict, List

import requests

API_BASE = "http://localhost:8777"         # <-- gateway FastAPI
PROJECT_NAME = "SDK v0.4 Demo (REST)"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# --------------------------------------------------------------------------- #
# Helper di basso livello – thin wrapper su requests                          #
# --------------------------------------------------------------------------- #

def api(method: str, path: str, **kwargs):
    """Esegue una chiamata HTTP e restituisce JSON (o lancia eccezione)."""
    url = f"{API_BASE}{path}"
    resp = requests.request(method, url, timeout=30, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"{method} {url} -> {resp.status_code} {resp.text}")
    if resp.content:
        return resp.json()
    return None


# --------------------------------------------------------------------------- #
# Utilities                                                                   #
# --------------------------------------------------------------------------- #

def html_description(title: str) -> str:
    return f"""
    <h3>{title}</h3>
    <p>Tracked via <strong>REST-demo workflow</strong>.</p>
    <table border="1" cellpadding="4" cellspacing="0">
        <tr><th>Status</th><td>Draft</td></tr>
        <tr><th>Owner</th><td>API-Bot</td></tr>
    </table>
    """


# --------------------------------------------------------------------------- #
# 1) Creazione dati demo                                                      #
# --------------------------------------------------------------------------- #

def create_demo_data() -> None:
    # -- Progetto
    prj = api("post", "/projects", json={"name": PROJECT_NAME})
    project_id = prj["id"]
    logging.info("Created project %s", project_id)

    # -- Colonne Scrum
    stage_ids: Dict[str, int] = {}
    for seq, name in enumerate(["Backlog", "To Do", "In Progress", "Done"], 1):
        st = api(
            "post",
            f"/projects/{project_id}/stages",
            json={"name": name, "sequence": seq, "fold": name == "Done"},
        )
        stage_ids[name] = st["id"]
        logging.info("  Stage %-12s → %s", name, st["id"])

    # -- Task principali
    today = date.today()
    parent = api(
        "post",
        "/tasks",
        json={
            "name": "Implement SDK core",
            "project_id": project_id,
            "stage_id": stage_ids["In Progress"],
            "description": html_description("Implement SDK core"),
            "date_deadline": (today + timedelta(days=10)).isoformat(),
        },
    )["id"]
    logging.info("Parent task id=%s", parent)

    others = [
        ("Define requirements", "Backlog", 5),
        ("Set up repository", "To Do", 3),
        ("Write documentation", "To Do", 12),
        ("Quality assurance", "In Progress", 14),
        ("Release v1.0", "Done", 15),
    ]
    for name, col, delta in others:
        t = api(
            "post",
            "/tasks",
            json={
                "name": name,
                "project_id": project_id,
                "stage_id": stage_ids[col],
                "description": html_description(name),
                "date_deadline": (today + timedelta(days=delta)).isoformat(),
            },
        )
        logging.info("  Task '%s' (id=%s → %s)", name, t["id"], col)

    # -- Sotto-task del parent
    for sub in ["REST wrapper", "CLI utility", "Unit tests"]:
        st = api(
            "post",
            "/tasks",
            json={
                "name": sub,
                "parent_id": parent,
                "project_id": project_id,
                "stage_id": stage_ids["In Progress"],
                "description": html_description(sub),
                "date_deadline": (today + timedelta(days=7)).isoformat(),
            },
        )
        logging.info("    Sub-task '%s' (id=%s)", sub, st["id"])

    # -- Sposta parent a Done (e prova a settare state=Done)
    api(
        "patch",
        f"/tasks/{parent}/move",
        params={"stage_id": stage_ids["Done"], "state_label": "Done"},
    )
    logging.info("Moved parent task → Done")

    # -- Riepilogo
    tasks = api("get", "/tasks", params={"project_id": project_id})
    print("\nTasks summary:")
    pprint(tasks)


# --------------------------------------------------------------------------- #
# 2) Pulizia interattiva                                                      #
# --------------------------------------------------------------------------- #

def interactive_cleanup() -> None:
    projs = api("get", "/projects", params={"name": PROJECT_NAME})
    if not projs:
        print("\nNessun progetto da eliminare.")
        return

    print(f"\nTrovati {len(projs)} progetti chiamati '{PROJECT_NAME}':")
    for p in projs:
        print(f"  • ID {p['id']}")

    for p in projs:
        pid = p["id"]
        # legge task e colonne
        tasks = api("get", "/tasks", params={"project_id": pid})
        stages = api("get", f"/projects/{pid}/stages") if hasattr(requests, "dummy") else []  # placeholder se non esposto

        task_ids = [t["id"] for t in tasks]
        stage_ids = [s["id"] for s in stages] if stages else []

        print(f"\n===> Eliminare progetto {pid}?")
        print(f"     Stage: {stage_ids or '—'}")
        print(f"     Task : {task_ids or '—'}")
        if input("Confermi? [y/N] ").strip().lower() != "y":
            print("  » salto.")
            continue

        for tid in task_ids:
            api("delete", f"/tasks/{tid}")
        for sid in stage_ids:
            api("delete", f"/stages/{sid}")
        api("delete", f"/projects/{pid}")
        print("  ✔ eliminato.")


# --------------------------------------------------------------------------- #
# main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> None:
    create_demo_data()
    interactive_cleanup()


if __name__ == "__main__":
    main()
