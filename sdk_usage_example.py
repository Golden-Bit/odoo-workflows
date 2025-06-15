#!/usr/bin/env python3
"""End-to-end demo for **odoo_sdk v0.4.0**

• Crea un progetto Scrum, task, sotto-task, sposta il task padre a *Done*
• Stampa un riepilogo compatto
• Alla fine esegue una **pulizia interattiva**:
  – trova tutti i progetti con lo stesso nome
  – per ciascuno chiede conferma e, in caso affermativo, elimina
    prima i task, poi gli stage, infine il progetto stesso
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pprint import pprint
from typing import Dict, List

from odoo_sdk import OdooClient   # ← importa lo SDK descritto

# --------------------------------------------------------------------------- #
# Configuration – replace with your own credentials                           #
# --------------------------------------------------------------------------- #
URL      = "YOUR_URL"
DB       = "YOUR_DB"
USER     = "YOUR_USERNAME"
API_KEY  = "YOUR_API_KEY"

PROJECT_NAME = "SDK v0.4 Demo"   # usato sia in creazione che in ricerca

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def html_description(title: str) -> str:
    """Ritorna un piccolo frammento HTML con tabella + logo."""
    return f"""
    <h3>{title}</h3>
    <p>This task is tracked via the <strong>SDK v0.4 demo workflow</strong>.</p>
    <table border="1" cellpadding="4" cellspacing="0">
        <tr><th>Status</th><td>Draft</td></tr>
        <tr><th>Owner</th><td>API-Bot</td></tr>
    </table>
    <p style="text-align:center;margin-top:8px;">
        <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/5/50/Odoo_logo.svg/600px-Odoo_logo.svg.png"
             width="120" alt="Odoo logo" />
    </p>"""


def create_demo_data(odoo: OdooClient) -> None:
    """Crea progetto, colonne e task di esempio."""
    project_id = odoo.create_project({"name": PROJECT_NAME})
    logging.info("Created project id=%s", project_id)

    # colonne Scrum
    stage_ids: Dict[str, int] = {}
    for seq, name in enumerate(["Backlog", "To Do", "In Progress", "Done"], 1):
        sid = odoo.create_stage(project_id, name, seq=seq, fold=name == "Done")
        stage_ids[name] = sid
        logging.info("  Stage %-12s → %s", name, sid)

    today = date.today()
    parent_task = odoo.create_task(
        {
            "name": "Implement SDK core",
            "project_id": project_id,
            "stage_id": stage_ids["In Progress"],
            "description": html_description("Implement SDK core"),
            "date_deadline": (today + timedelta(days=10)).isoformat(),
        }
    )
    logging.info("  Parent task id=%s", parent_task)

    others = [
        ("Define requirements", "Backlog", 5),
        ("Set up repository", "To Do", 3),
        ("Write documentation", "To Do", 12),
        ("Quality assurance", "In Progress", 14),
        ("Release v1.0", "Done", 15),
    ]
    for name, col, delta in others:
        tid = odoo.create_task(
            {
                "name": name,
                "project_id": project_id,
                "stage_id": stage_ids[col],
                "description": html_description(name),
                "date_deadline": (today + timedelta(days=delta)).isoformat(),
            }
        )
        logging.info("  Task '%s' (id=%s → %s)", name, tid, col)

    # sotto-task
    for sub in ["REST wrapper", "CLI utility", "Unit tests"]:
        stid = odoo.create_subtask(
            parent_id=parent_task,
            values={
                "name": sub,
                "project_id": project_id,
                "stage_id": stage_ids["In Progress"],
                "description": html_description(sub),
                "date_deadline": (today + timedelta(days=7)).isoformat(),
            },
        )
        logging.info("    Sub-task '%s' (id=%s)", sub, stid)

    # sposta il task padre a Done
    odoo.move_task(parent_task, stage_ids["Done"], state_label="Done")
    logging.info("Moved parent task %s → Done", parent_task)

    # riepilogo
    tasks = odoo.search_read(
        "project.task",
        [("project_id", "=", project_id)],
        fields=["name", "stage_id", "parent_id", "state", "date_deadline"],
        order="stage_id, id",
    )
    print("\nCreated demo tasks:")
    pprint(tasks)


def interactive_cleanup(odoo: OdooClient) -> None:
    """Trova e (se confermato) elimina progetti omonimi con i loro contenuti."""
    projects = odoo.search_read(
        "project.project",
        [["name", "=", PROJECT_NAME]],
        fields=["id", "name"],
    )
    if not projects:
        print("\nNessun progetto da eliminare.")
        return

    print(f"\nTrovati {len(projects)} progetti chiamati '{PROJECT_NAME}':")
    for prj in projects:
        print(f"  • ID {prj['id']} – {prj['name']}")

    for prj in projects:
        pid = prj["id"]
        # task e stage collegati
        task_ids: List[int] = odoo.search("project.task", [["project_id", "=", pid]])
        stage_ids: List[int] = odoo.search(
            "project.task.type",
            [["project_ids", "in", [pid]]],
        )

        print(f"\n===> Pronto a eliminare progetto {pid} «{prj['name']}»")
        print(f"     Stage: {stage_ids}")
        print(f"     Task : {task_ids}")
        ans = input("Confermi cancellazione completa? [y/N] ").strip().lower()
        if ans != "y":
            print("  » salto questo progetto.")
            continue

        # 1. cancella task
        if task_ids:
            print("  • deleting tasks …")
            odoo.delete("project.task", task_ids)

        # 2. cancella colonne
        if stage_ids:
            print("  • deleting stages …")
            odoo.delete_stage(stage_ids[0]) if len(stage_ids) == 1 else odoo.delete("project.task.type", stage_ids)

        # 3. cancella progetto
        print("  • deleting project …")
        odoo.delete_project(pid)
        print("  ✔ eliminato.\n")


def main() -> None:
    odoo = OdooClient(URL, DB, USER, API_KEY)
    create_demo_data(odoo)
    interactive_cleanup(odoo)


if __name__ == "__main__":
    main()
