"""odoo_api.py – REST gateway for Odoo 18 via *odoo_sdk v0.4*
================================================================
This single‑file FastAPI application wraps all the high‑level helpers exposed
by **odoo_sdk v0.4** (projects, stages, tasks, attachments, bulk‑write, …)
in a clean, well‑documented REST API – ready to be consumed by an LLM‑powered
*MCP* stack or a GPT on ChatGPT.

Key design goals
----------------
* **1‑to‑1 mapping** – Every public SDK helper has an HTTP endpoint.
* **Stateless** – Each request receives its own authenticated
  :class:`~odoo_sdk.OdooClient`; TCP session pooling reduces overhead.
* **Rich OpenAPI** – Detailed summaries *and* long‑form descriptions with
  concrete examples for every route, so that an agent can discover the API
  autonomously.
* **LLM friendly** – Simple JSON payloads; many2one fields are ‘flattened’
  in responses (``[id, label] → id``) and dates are ISO‑8601 ``YYYY‑MM‑DD``.
* **Minimal deps** – Only *fastapi*, *uvicorn[standard]*, *pydantic* and
  *requests* (pulled in by the SDK).

Usage quick‑start
~~~~~~~~~~~~~~~~~
export ODOO_URL=https://my.odoo.com/jsonrpc \
        ODOO_DB=my ODOO_USER=bot@my.com ODOO_API_KEY=***
uvicorn odoo_api:app --reload --port 8777

Then browse http://localhost:8777/docs for the interactive Swagger UI.

"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from odoo_sdk import OdooClient, JSON  # ← your improved SDK v0.4 import

######################################################################
# FastAPI – app & dependency                                          #
######################################################################

app = FastAPI(
    title="Odoo 18 JSON‑RPC Gateway",
    version="0.4.0",
    summary=(
        "Thin REST facade around *odoo_sdk* helpers – optimised for LLMs "
        "and autonomous agents."
    ),
    description=(
        "This service converts Odoo's low‑level JSON‑RPC interface into a set "
        "of human‑readable, self‑describing REST endpoints. Each route mirrors "
        "one of the high‑level helpers provided by *odoo_sdk*. The API uses "
        "pragmatic JSON payloads and normalises Odoo's many‑to‑one responses "
        "to plain integers so that language models can reason over them with "
        "minimal schema friction."
    ),
    contact={
        "name": "API Support",
        "url": "https://github.com/your-org/odoo-sdk",
        "email": "support@example.com",
    },
    root_path="/odoo-api"
)


def get_client() -> OdooClient:  # dependency
    """Return an **authenticated** :class:`OdooClient` for the current request.

    The credentials are injected via *env‑vars* – adjust them on your server or
    in a docker‑compose file. For demo purposes the fallback below hard‑codes
    the public Odoo *Sandbox* instance – **do _not_ keep this in production!**
    """

    url = os.getenv("ODOO_URL", "URL")
    db = os.getenv("ODOO_DB", "DB")
    user = os.getenv("ODOO_USER", "USER")
    key = os.getenv("ODOO_API_KEY", "KEY")


    with OdooClient(url, db, user, key) as odoo:
        yield odoo  # FastAPI closes the context‑manager after the response

######################################################################
# Utility – normalise Odoo records for Pydantic                      #
######################################################################


def _m2o_id(val):
    """Return the *id* of a many2one tuple ``[id, label]`` (or the input)."""
    return val[0] if isinstance(val, (list, tuple)) else val


def _normalize_task(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten M2O fields and strip the time part from *date_deadline*."""
    out = rec.copy()
    out["project_id"] = _m2o_id(out.get("project_id"))
    out["stage_id"] = _m2o_id(out.get("stage_id"))
    if out.get("parent_id"):
        out["parent_id"] = _m2o_id(out["parent_id"])
    if out.get("date_deadline"):
        out["date_deadline"] = out["date_deadline"][:10]
    return out

######################################################################
# Pydantic schemas                                                   #
######################################################################

class _ConfigMixin:
    """Add a lazily‑overridden *examples* placeholder for OpenAPI."""

    model_config = {"json_schema_extra": {"examples": []}}


class ProjectIn(_ConfigMixin, BaseModel):
    """Input model for *project.project* creation/updating."""

    name: str = Field(..., description="Human‑readable project name")

    model_config = {"json_schema_extra": {"examples": [{"name": "Website revamp"}]}}


class ProjectOut(ProjectIn):
    id: int = Field(..., description="Internal Odoo ID (primary key)")


class StageIn(_ConfigMixin, BaseModel):
    """Input model for *project.task.type* (Kanban column)."""

    name: str = Field(..., description="Column label, shown in the UI")
    sequence: int = Field(10, description="Ascending order in the Kanban view")
    fold: bool = Field(False, description="Collapse column by default when *True*")

    model_config = {"json_schema_extra": {"examples": [{"name": "QA", "sequence": 30}]}}


class StageOut(StageIn):
    id: int = Field(..., description="Internal Odoo ID")


class TaskIn(_ConfigMixin, BaseModel):
    """Input model for *project.task* creation/updating."""

    name: str = Field(..., description="Task title")
    project_id: int = Field(..., description="Owning project (id)")
    stage_id: int = Field(..., description="Current Kanban column (id)")
    description: Optional[str] = Field(None, description="Rich‑text HTML body")
    date_deadline: Optional[str] = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Due date in YYYY‑MM‑DD format",
    )
    parent_id: Optional[int] = Field(None, description="Parent task id (for sub‑tasks)")

    model_config = {"json_schema_extra": {"examples": [{
        "name": "Implement REST API",
        "project_id": 12,
        "stage_id": 44,
        "date_deadline": "2025-07-01",
        "description": "<p>Document every endpoint</p>"
    }]}}


class TaskOut(TaskIn):
    id: int = Field(..., description="Internal Odoo ID")
    state: Optional[str] = Field(None, description="Internal *state* code (if any)")


class BulkWriteIn(BaseModel):
    values: Dict[int, Dict[str, Any]] = Field(
        ..., description="Mapping {id: {field: value}} for mass updates",
        examples=[{101: {"active": False}, 102: {"stage_id": 55}}],
    )

######################################################################
# Version endpoint                                                   #
######################################################################

@app.get(
    "/version",
    response_model=Dict[str, str],
    summary="Return Odoo & SDK versions",
    description="Helper route so automated clients can verify both the remote "
    "Odoo deployment and the SDK wrapper version they are talking to.",
)
def get_versions(odoo: OdooClient = Depends(get_client)):
    """Returns a dict ``{"odoo": "...", "sdk": "..."}``."""
    return {"odoo": odoo.version()["server_version"], "sdk": odoo.__version__}

######################################################################
# Project endpoints                                                  #
######################################################################

@app.post(
    "/projects",
    status_code=201,
    response_model=ProjectOut,
    summary="Create a new project",
    description="Wraps :meth:`OdooClient.create_project`. Only *name* is "
                "required, all other fields follow Odoo defaults.",
)
def create_project(payload: ProjectIn, odoo: OdooClient = Depends(get_client)):
    pid = odoo.create_project(payload.model_dump())
    return {"id": pid, **payload.model_dump()}


@app.get(
    "/projects",
    response_model=List[ProjectOut],
    summary="List or search projects",
    description="If *name* query param is provided a case‑insensitive "
                "substring search is performed (ILike).",
)

def list_projects(name: Optional[str] = None, odoo: OdooClient = Depends(get_client)):
    dom = [["name", "ilike", name]] if name else []
    return odoo.search_read("project.project", dom, fields=["id", "name"])


@app.put(
    "/projects/{project_id}",
    response_model=ProjectOut,
    summary="Update a project",
)

def update_project(project_id: int, payload: ProjectIn, odoo: OdooClient = Depends(get_client)):
    if not odoo.update_project(project_id, payload.model_dump()):
        raise HTTPException(404, "Project not found")
    return {"id": project_id, **payload.model_dump()}


@app.patch(
    "/projects/{project_id}/archive",
    summary="Archive / restore a project",
    description="Soft‑deletes a project by toggling the *active* flag instead "
                "of unlinking – this keeps analytic lines & timesheets safe.",
)

def archive_project(project_id: int, active: bool = False, odoo: OdooClient = Depends(get_client)):
    odoo.archive_project(project_id, active=active)
    return {"id": project_id, "active": active}


@app.delete(
    "/projects/{project_id}",
    status_code=204,
    summary="Hard‑delete a project",
    description="Irreversibly removes the record. Make sure to delete or move "
                "linked tasks first, or Odoo will raise a constraint error.",
)

def delete_project(project_id: int, odoo: OdooClient = Depends(get_client)):
    if not odoo.delete_project(project_id):
        raise HTTPException(404, "Project not found")

######################################################################
# Stage endpoints                                                    #
######################################################################

@app.post(
    "/projects/{project_id}/stages",
    status_code=201,
    response_model=StageOut,
    summary="Add a Kanban column to a project",
    description="Wrapper around :meth:`OdooClient.create_stage`. Pass the "
                "canonical *sequence* value expected by Odoo (10, 20, 30…).",
)

def create_stage(project_id: int, payload: StageIn, odoo: OdooClient = Depends(get_client)):
    data = payload.model_dump()
    sid = odoo.create_stage(
        project_id,
        name=data["name"],
        seq=data["sequence"],
        fold=data["fold"],
    )
    return {"id": sid, **data}


@app.put(
    "/stages/{stage_id}",
    response_model=StageOut,
    summary="Update a Kanban column",
)

def update_stage(stage_id: int, payload: StageIn, odoo: OdooClient = Depends(get_client)):
    if not odoo.update_stage(stage_id, payload.model_dump()):
        raise HTTPException(404, "Stage not found")
    return {"id": stage_id, **payload.model_dump()}


@app.patch(
    "/stages/{stage_id}/archive",
    summary="Archive / restore a column",
)

def archive_stage(stage_id: int, active: bool = False, odoo: OdooClient = Depends(get_client)):
    odoo.archive_stage(stage_id, active=active)
    return {"id": stage_id, "active": active}


@app.delete(
    "/stages/{stage_id}",
    status_code=204,
    summary="Hard‑delete a column",
)

def delete_stage(stage_id: int, odoo: OdooClient = Depends(get_client)):
    if not odoo.delete_stage(stage_id):
        raise HTTPException(404, "Stage not found")

######################################################################
# Task endpoints                                                     #
######################################################################

@app.post(
    "/tasks",
    status_code=201,
    response_model=TaskOut,
    summary="Create a task (or sub‑task)",
    description="All fields map 1‑to‑1 to the SDK helper. To create a sub‑task "
                "simply set *parent_id* to the parent task id.",
)

def create_task(payload: TaskIn, odoo: OdooClient = Depends(get_client)):
    tid = odoo.create_task(payload.model_dump())
    return {"id": tid, **payload.model_dump()}


@app.get(
    "/tasks",
    response_model=List[TaskOut],
    summary="Search / list tasks",
    description="Optional query param *project_id* restricts the result set. "
                "Data are normalised so M2O fields are plain integers and "
                "dates are truncated to `YYYY‑MM‑DD`.",
)

def list_tasks(project_id: Optional[int] = None, odoo: OdooClient = Depends(get_client)):
    dom = [["project_id", "=", project_id]] if project_id else []
    raw = odoo.search_read(
        "project.task",
        dom,
        fields=[
            "id", "name", "project_id", "stage_id",
            "state", "parent_id", "date_deadline",
        ],
    )
    return [_normalize_task(r) for r in raw]


@app.put(
    "/tasks/{task_id}",
    response_model=TaskOut,
    summary="Update a task",
)

def update_task(task_id: int, payload: TaskIn, odoo: OdooClient = Depends(get_client)):
    if not odoo.update_task(task_id, payload.model_dump()):
        raise HTTPException(404, "Task not found")
    return {"id": task_id, **payload.model_dump()}


@app.patch(
    "/tasks/{task_id}/move",
    summary="Move a task to another column and/or state",
    description="Internally calls :py:meth:`OdooClient.move_task`. Pass the new "
                "Kanban *stage_id* and optionally the human‑readable state "
                "label (e.g. `Done`). The helper converts the label to the "
                "internal code, if present.",
)

def move_task(task_id: int, stage_id: int, state_label: Optional[str] = None, odoo: OdooClient = Depends(get_client)):
    odoo.move_task(task_id, stage_id, state_label=state_label)
    return {"task_id": task_id, "stage_id": stage_id, "state_label": state_label}


@app.delete(
    "/tasks/{task_id}",
    status_code=204,
    summary="Hard‑delete a task",
)

def delete_task(task_id: int, odoo: OdooClient = Depends(get_client)):
    if not odoo.delete_task(task_id):
        raise HTTPException(404, "Task not found")

######################################################################
# Attachment endpoints                                               #
######################################################################

@app.post(
    "/tasks/{task_id}/attachments",
    status_code=201,
    summary="Attach a file to a task",
    description="Uses :py:meth:`OdooClient.attach_file`. The uploaded stream is "
                "stored in /tmp only for the duration of the request.",
)

def upload_attachment(task_id: int, file: UploadFile = File(...), odoo: OdooClient = Depends(get_client)):
    tmp = Path(f"/tmp/{file.filename}")
    tmp.write_bytes(file.file.read())
    aid = odoo.attach_file(task_id, tmp)
    tmp.unlink(missing_ok=True)
    return {"attachment_id": aid}


@app.get(
    "/tasks/{task_id}/attachments",
    summary="List attachments for a task",
)

def list_task_attachments(task_id: int, odoo: OdooClient = Depends(get_client)):
    return odoo.list_attachments("project.task", task_id)

######################################################################
# Bulk‑write endpoint                                                #
######################################################################

@app.post(
    "/bulk/{model}",
    summary="Mass‑update any model via *execute_batch*",
    description="The path parameter *model* must be the technical model name "
                "(e.g. `project.task`). The body maps record ids to the field "
                "values you want to write. Runs in a single RPC round‑trip.",
)

def bulk_write(model: str, body: BulkWriteIn, odoo: OdooClient = Depends(get_client)):
    result = odoo.bulk_write(model, body.values)
    return {"updated": len(result)}
