"""Odoo SDK · JSON-RPC helper for Odoo 18
================================================
A *batteries-included* Python client wrapping the official JSON-RPC
interface of Odoo 18. Targeted at ML / LLM tools that need to ingest or
manipulate Odoo data programmatically.

Highlights
~~~~~~~~~~
* **Single dependency:** only `requests`.
* **Context-manager** support ⇒ automatic `authenticate()` on `__enter__`.
* **Extensive docstrings & type-hints** ready for IDE / LSP autocompletion.
* **Generic CRUD**, **metadata** helpers (`fields_get`, selections),
  **batch execute**, and convenience wrappers for **Project** / **Task**
  workflows (create sub-task, move task, attach files, etc.).
* **Safe retries** on transient HTTP errors (exponential back-off).
* 100 % JSON-RPC compliance – no private endpoints.

Example
-------
```python
from odoo_sdk import OdooClient
from pathlib import Path

with OdooClient(url="https://my.odoo.com/jsonrpc",
                db="my",
                username="bot@example.com",
                api_key="***") as odoo:
    prj = odoo.create_project({"name": "LLM Playground"})
    task = odoo.create_subtask(
        parent_id=odoo.create_task({"name": "Root", "project_id": prj}),
        values={"name": "Write docs", "description": "LLM-ready!"}
    )
    odoo.attach_file(task, Path("README.md"))
```
"""
from __future__ import annotations
import base64
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

import requests
from requests import Response, Session

__all__ = ["OdooClient", "RPCError", "AuthenticationError"]
__version__ = "0.4.0"  # keep in sync with pyproject.toml when packaging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

JSON = Dict[str, Any]
Domain = List[List[Any]]

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #

class RPCError(RuntimeError):
    """Raised when the JSON-RPC endpoint returns an error object."""

class AuthenticationError(RPCError):
    """Raised when credentials are invalid or `uid` cannot be retrieved."""

# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class OdooClient:
    """High-level JSON-RPC wrapper.

    Parameters
    ----------
    url:
        The `/jsonrpc` endpoint, e.g. `https://example.odoo.com/jsonrpc`.
    db:
        Database name (visible in *Manage Databases* or sub-domain).
    username:
        Login of the API user.
    api_key:
        API-key or password for the user.
    timeout:
        Requests timeout in **seconds** (default 30).
    verify_ssl:
        Verify TLS certificates (default True).
    session:
        Optional `requests.Session` to reuse TCP connections.
    """

    _COMMON = "common"
    _OBJECT = "object"

    # ---------------------------- lifecycle ---------------------------------
    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        api_key: str,
        *,
        timeout: int = 30,
        verify_ssl: bool = True,
        session: Optional[Session] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._sess: Session = session or Session()
        self.uid: Optional[int] = None

    # ------------------------- context manager ------------------------------
    def __enter__(self) -> "OdooClient":
        self.authenticate()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        # Nothing to dispose – keep session open for re-use.
        pass

    # -------------------------- private helpers -----------------------------
    def _post(self, payload: JSON) -> Response:
        """Low-level POST with safe retry on *ConnectionError* and 502/504."""
        for attempt in range(3):
            try:
                resp = self._sess.post(
                    self.url,
                    json=payload,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.HTTPError) as exc:
                if attempt == 2 or isinstance(exc, requests.HTTPError) and exc.response.status_code < 500:
                    raise
                wait = 2 ** attempt
                logger.warning("Transient network error (%s) – retrying in %ss", exc, wait)
                time.sleep(wait)
        raise RuntimeError("Unreachable")  # pragma: no cover

    def _json_rpc(self, service: str, method: str, args: Sequence[Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": list(args)},
            "id": random.randint(1, 1_000_000),
        }
        logger.debug("RPC → %s", json.dumps(payload, indent=2)[:500])
        resp = self._post(payload).json()
        if "error" in resp:
            logger.error("RPC error: %s", resp["error"])
            raise RPCError(resp["error"])
        return resp.get("result")

    # --------------------------- core methods -------------------------------
    def authenticate(self) -> int:
        """Authenticate and cache *uid* (lazy – called automatically).

        Raises
        ------
        AuthenticationError
            If credentials are invalid.
        """
        result = self._json_rpc(
            self._COMMON,
            "authenticate",
            [self.db, self.username, self.api_key, {}],
        )
        if not isinstance(result, int):
            raise AuthenticationError(result)
        self.uid = result
        logger.info("Authenticated uid=%s", self.uid)
        return result

    def execute_kw(self, model: str, method: str, *args: Sequence[Any], **kwargs: JSON) -> Any:
        """Thin wrapper around ``object.execute_kw`` with auto-auth."""
        if self.uid is None:
            self.authenticate()
        rpc_args = [self.db, self.uid, self.api_key, model, method, list(args), kwargs or {}]
        return self._json_rpc(self._OBJECT, "execute_kw", rpc_args)

    # ----------------------------- utilities --------------------------------
    def fields_get(self, model: str, attributes: Sequence[str] | None = None) -> JSON:
        """Return metadata for *model* (uses ``fields_get``)."""
        return self.execute_kw(model, "fields_get", [], attributes=attributes or [])

    def selection_labels(self, model: str, field: str) -> Dict[str, str]:
        """Return a mapping *code → label* for a *selection* field."""
        meta = self.fields_get(model, attributes=["selection"])
        return dict(meta[field]["selection"])  # type: ignore[index]

    # ----------------------- generic CRUD wrappers --------------------------
    def create(self, model: str, values: JSON) -> int:
        return int(self.execute_kw(model, "create", values))

    def read(self, model: str, ids: Sequence[int], fields: Sequence[str] | None = None) -> List[JSON]:
        return list(self.execute_kw(model, "read", ids, fields or []))

    def update(self, model: str, ids: Sequence[int], values: JSON) -> bool:
        return bool(self.execute_kw(model, "write", ids, values))

    def delete(self, model: str, ids: Sequence[int]) -> bool:
        return bool(self.execute_kw(model, "unlink", ids))

    def search(self, model: str, domain: Domain | None = None, *, offset: int = 0, limit: int | None = None, order: str | None = None) -> List[int]:
        return list(self.execute_kw(model, "search", domain or [], offset, limit, order))

    def search_read(self, model: str, domain: Domain | None = None, *, fields: Sequence[str] | None = None, offset: int = 0, limit: int | None = None, order: str | None = None) -> List[JSON]:
        opts: JSON = {}
        if fields is not None:
            opts["fields"] = list(fields)
        if offset:
            opts["offset"] = offset
        if limit is not None:
            opts["limit"] = limit
        if order:
            opts["order"] = order
        return list(self.execute_kw(model, "search_read", domain or [], **opts))

    def search_count(self, model: str, domain: Domain | None = None) -> int:
        return int(self.execute_kw(model, "search_count", domain or []))

    # ----------------------- batch / pipeline utils -------------------------
    def execute_batch(self, calls: Iterable[Mapping[str, Any]]) -> List[Any]:
        """Execute a list of method calls in a single round-trip.

        Each item must be a mapping with keys: ``model``, ``method``,
        ``args`` (list) and optional ``kwargs`` (dict).
        """
        return self._json_rpc(self._OBJECT, "execute", [
            self.db,
            self.uid or self.authenticate(),
            self.api_key,
            calls,
        ])

    # --------------------------------------------------------------------- #
    # Project helpers
    # --------------------------------------------------------------------- #
    _PROJECT_MODEL = "project.project"

    def create_project(self, values: JSON) -> int:  # noqa: D401 – verb imperative
        return self.create(self._PROJECT_MODEL, values)

    def update_project(self, project_id: int, values: JSON) -> bool:
        return self.update(self._PROJECT_MODEL, [project_id], values)

    # --------------------------------------------------------------------- #
    # Task helpers
    # --------------------------------------------------------------------- #
    _TASK_MODEL = "project.task"

    # Convenience wrappers --------------------------------------------------
    def create_task(self, values: JSON) -> int:
        return self.create(self._TASK_MODEL, values)

    def create_subtask(self, parent_id: int, values: JSON) -> int:
        vals = dict(values)
        vals["parent_id"] = parent_id
        return self.create_task(vals)

    def set_task_description(self, task_id: int, html: str) -> bool:
        return self.update_task(task_id, {"description": html})

    def move_task(self, task_id: int, stage_id: int, *, state_label: str | None = None) -> bool:
        vals: JSON = {"stage_id": stage_id}
        if state_label:
            try:
                sel = self.selection_labels(self._TASK_MODEL, "state")
                code = next(c for c, lbl in sel.items() if lbl.lower() == state_label.lower())
                vals["state"] = code
            except (StopIteration, KeyError):
                logger.warning("State label '%s' not found – only stage moved", state_label)
        return self.update_task(task_id, vals)

    def update_task(self, task_id: int, values: JSON) -> bool:
        return self.update(self._TASK_MODEL, [task_id], values)

    def attach_file(self, res_id: int, file_path: Path | str, *, model: str | None = None, filename: str | None = None, mimetype: str | None = None) -> int:
        """Upload a file as ``ir.attachment`` linked to *model*.*res_id*.

        Parameters
        ----------
        res_id:
            ID of the record the attachment is linked to.
        file_path:
            Path to the file.
        model:
            Model name; defaults to :pyattr:`_TASK_MODEL` if omitted.
        filename, mimetype:
            Optional meta; autodetected if not provided.
        """

        p = Path(file_path)
        data = base64.b64encode(p.read_bytes()).decode()  # Python-3 way :contentReference[oaicite:0]{index=0}

        return self.create(
            "ir.attachment",
            {
                "name": filename or p.name,
                "datas": data,
                "res_model": model or self._TASK_MODEL,
                "res_id": res_id,
                "mimetype": mimetype or "application/octet-stream",
            },
        )

    # ------------------------------------------------------------------ #
    #  Project / Stage / Task  —  CRUD & Utility                         #
    # ------------------------------------------------------------------ #

    # -------- Project --------------------------------------------------
    def delete_project(self, project_id: int) -> bool:
        """Remove a *project.project* (hard delete).

        Nota: per preservare la cronologia considera :meth:`archive_project`.
        """
        return self.delete(self._PROJECT_MODEL, [project_id])

    def archive_project(self, project_id: int, *, active: bool = False) -> bool:
        """Archivia (o ri-attiva) un progetto impostando ``active``.
        Usare invece di `unlink` quando si vuole mantenere task e timesheet.•"""
        return self.update_project(project_id, {"active": active})

    # -------- Stage (project.task.type) --------------------------------
    _STAGE_MODEL = "project.task.type"

    def create_stage(self, project_id: int, name: str, *,
                     seq: int = 10, fold: bool = False) -> int:
        """Crea una *Kanban column* legata a un singolo progetto."""
        return self.create(self._STAGE_MODEL,
                           {"name": name,
                            "sequence": seq,
                            "fold": fold,
                            "project_ids": [(4, project_id)]})

    def update_stage(self, stage_id: int, values: JSON) -> bool:
        """Aggiorna una colonna Kanban.

        Esempi
        -------
        odoo.update_stage(12, {"name": "QA / Review", "sequence": 25})
        True
        odoo.update_stage(15, {"fold": True})          # collassa la colonna
        True

        Parameters
        ----------
        stage_id:
            ID della *project.task.type* da modificare.
        values:
            Dizionario campo→valore come per il classico `write`.
        """
        return self.update(self._STAGE_MODEL, [stage_id], values)

    def archive_stage(self, stage_id: int, *, active: bool = False) -> bool:
        """Attiva/disattiva una *project.task.type* senza cancellarla.

        Utile quando esistono task collegati (Odoo blocca l’unlink se la colonna
        non è vuota).  :contentReference[oaicite:3]{index=3}
        """
        return self.update(self._STAGE_MODEL, [stage_id], {"active": active})

    def delete_stage(self, stage_id: int) -> bool:
        """Elimina una colonna.
        Odoo impedisce l’unlink se ci sono task associati → gestire prima la migrazione
        dei task (vedi :meth:`move_task`).  :contentReference[oaicite:0]{index=0}"""
        return self.delete(self._STAGE_MODEL, [stage_id])

    # -------- Task -----------------------------------------------------
    def delete_task(self, task_id: int) -> bool:
        """Hard-delete un singolo task."""
        return self.delete(self._TASK_MODEL, [task_id])

    def archive_task(self, task_id: int, *, active: bool = False) -> bool:
        """Archivia o ri-attiva un task (toggle campo *active*)."""
        return self.update_task(task_id, {"active": active})

    def assign_task(self, task_id: int, user_id: int, *,
                    add_follower: bool = True) -> bool:
        """Assegna il task a un utente e, opzionalmente, lo aggiunge come follower
        per notifiche automatiche.  :contentReference[oaicite:1]{index=1}"""
        vals: JSON = {"user_id": user_id}
        if add_follower:
            vals["message_follower_ids"] = [(4, user_id)]
        return self.update_task(task_id, vals)

    # ------------------------------------------------------------------ #
    #  Advanced helpers                                                  #
    # ------------------------------------------------------------------ #

    def copy_record(self, model: str, record_id: int,
                    defaults: Optional[JSON] = None) -> int:
        """Duplica un record usando il metodo ORM ``copy``.
        Utile per clonare task template o interi progetti.  :contentReference[oaicite:2]{index=2}"""
        return int(self.execute_kw(model, "copy", [record_id], defaults or {}))

    def iter_search_read(self, model: str, domain: Domain | None = None, *,
                         batch: int = 100, **opts) -> Iterable[JSON]:
        """Generatore che restituisce pagine successive senza esporre `offset/limit`."""
        offset = 0
        while True:
            records = self.search_read(model, domain,
                                       offset=offset, limit=batch, **opts)
            if not records:
                break
            yield from records
            offset += len(records)      # avanza esattamente di quanto restituito

    def read_group(self, model: str, fields: Sequence[str],
                   groupby: Sequence[str], domain: Domain | None = None) -> List[JSON]:
        """Aggregazioni server-side (somma, conteggio, media, …).  :contentReference[oaicite:4]{index=4}"""
        return list(self.execute_kw(model, "read_group",
                                    domain or [], fields, groupby))

    # ------------------------------------------------------------------ #
    #  Attachment utilities                                              #
    # ------------------------------------------------------------------ #

    def list_attachments(self, res_model: str, res_id: int,
                         *, fields: Sequence[str] | None = None) -> List[JSON]:
        """Elenca gli allegati di un record.  :contentReference[oaicite:5]{index=5}"""
        return self.search_read("ir.attachment",
                                [["res_model", "=", res_model],
                                 ["res_id", "=", res_id]],
                                fields=fields or ["name", "mimetype", "datas_fname"])


    # --------------------------------------------------------------------- #
    #
    # --------------------------------------------------------------------- #

    def bulk_write(self, model: str, id_vals_map: Mapping[int, JSON]) -> List[Any]:

        """Aggiornamenti massivi con una sola round-trip.

        Parameters
        ----------
        model:
            Nome del modello (es. ``project.task``).
        id_vals_map:
            Dict ``{record_id: {field: value, …}}``.
        """

        calls = [
            {
                "model": model,
                "method": "write",
                "args": [[rid], vals],
                "kwargs": {},
            }
            for rid, vals in id_vals_map.items()
        ]
        return self.execute_batch(calls)  # usa execute_batch interno :contentReference[oaicite:5]{index=5}

    # --------------------------------------------------------------------- #
    # Misc
    # --------------------------------------------------------------------- #
    def version(self) -> JSON:
        return self._json_rpc(self._COMMON, "version", [])
