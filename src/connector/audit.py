"""Audit log: who called which tool, when, with what outcome. Never content, never secrets."""
import json
import sys
from datetime import datetime, timezone
from typing import Callable, Optional


class AuditLogger:
    def __init__(self, path: Optional[str] = None, sink: Optional[Callable[[str], None]] = None):
        self._path = path or None
        self._sink = sink            # tests pass list.append here

    def record(self, *, request_id: str, tenant_id: str, credential_id: str,
               tool: str, status: str, latency_ms: float) -> None:
        line = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "request_id": request_id,
            "tenant_id": tenant_id,
            "credential_id": credential_id,
            "tool": tool,
            "status": status,
            "latency_ms": round(latency_ms, 1),
        }, separators=(",", ":"))

        if self._sink is not None:
            self._sink(line)
            return
        # stderr only: stdout belongs to the MCP protocol
        print(line, file=sys.stderr, flush=True)
        if self._path:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as exc:   # a broken log file must never break a tool call
                print(f"audit write failed: {exc}", file=sys.stderr, flush=True)