"""
backend/pipeline/metrics.py
============================
Pipeline run metrics writer — records stats to pipeline_runs table (migration 013).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Gemini Flash pricing — update this if Google changes rates
_GEMINI_INPUT_COST_PER_TOKEN  = 0.075  / 1_000_000   # $0.075 / 1M input tokens
_GEMINI_OUTPUT_COST_PER_TOKEN = 0.30   / 1_000_000   # $0.30  / 1M output tokens
_GEMINI_AVG_COST_PER_TOKEN    = (_GEMINI_INPUT_COST_PER_TOKEN * 0.70 +
                                  _GEMINI_OUTPUT_COST_PER_TOKEN * 0.30)


@dataclass
class PipelineRun:
    pipeline:    str
    run_date:    str
    dag_run_id:  str = ""
    chunk_index: int = -1

    total_processed: int = 0
    success_count:   int = 0
    failed_count:    int = 0
    skipped_count:   int = 0
    gemini_calls:    int = 0
    gemini_tokens:   int = 0

    _started_at: float         = field(default_factory=time.time, init=False, repr=False)
    _errors:     list[str]     = field(default_factory=list,       init=False, repr=False)
    _meta:       dict[str,Any] = field(default_factory=dict,       init=False, repr=False)

    def record_item(
        self,
        status:        str,
        gemini_tokens: int = 0,
        error:         str = "",
        reason:        str = "",
    ) -> None:
        self.total_processed += 1
        if status == "success":
            self.success_count += 1
        elif status == "failed":
            self.failed_count += 1
            if error and len(self._errors) < 10:
                self._errors.append(error[:300])
        elif status == "skipped":
            self.skipped_count += 1
        if gemini_tokens:
            self.gemini_calls  += 1
            self.gemini_tokens += gemini_tokens

    def set_meta(self, **kwargs) -> None:
        self._meta.update(kwargs)

    def success_rate(self) -> float:
        eligible = self.total_processed - self.skipped_count
        return self.success_count / eligible if eligible > 0 else 1.0

    def estimated_cost_usd(self) -> float:
        return round(self.gemini_tokens * _GEMINI_AVG_COST_PER_TOKEN, 6)

    def finish(self, db_url: Optional[str] = None) -> None:
        duration = round(time.time() - self._started_at, 1)
        row = {
            "pipeline":           self.pipeline,
            "run_date":           self.run_date,
            "dag_run_id":         self.dag_run_id or "",
            "chunk_index":        self.chunk_index,
            "started_at":         datetime.fromtimestamp(self._started_at, tz=timezone.utc).isoformat(),
            "finished_at":        datetime.now(tz=timezone.utc).isoformat(),
            "duration_seconds":   duration,
            "total_processed":    self.total_processed,
            "success_count":      self.success_count,
            "failed_count":       self.failed_count,
            "skipped_count":      self.skipped_count,
            "gemini_calls":       self.gemini_calls,
            "gemini_tokens_used": self.gemini_tokens,
            "estimated_cost_usd": self.estimated_cost_usd(),
            "error_sample":       json.dumps(self._errors),
            "metadata":           json.dumps(self._meta),
        }

        logger.info(
            "PipelineRun | pipeline=%s run_date=%s chunk=%d "
            "processed=%d success=%d failed=%d skipped=%d "
            "gemini=%d tokens=%d cost=$%.6f duration=%.1fs",
            self.pipeline, self.run_date, self.chunk_index,
            self.total_processed, self.success_count,
            self.failed_count, self.skipped_count,
            self.gemini_calls, self.gemini_tokens,
            self.estimated_cost_usd(), duration,
        )

        url = db_url or os.environ.get("DATABASE_URL", "")
        if not url:
            logger.warning("DATABASE_URL not set — pipeline_runs row not persisted")
            return

        try:
            import psycopg2
            conn = psycopg2.connect(url, connect_timeout=10)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pipeline_runs (
                        pipeline, run_date, dag_run_id, chunk_index,
                        started_at, finished_at, duration_seconds,
                        total_processed, success_count, failed_count, skipped_count,
                        gemini_calls, gemini_tokens_used, estimated_cost_usd,
                        error_sample, metadata
                    ) VALUES (
                        %(pipeline)s, %(run_date)s, %(dag_run_id)s, %(chunk_index)s,
                        %(started_at)s, %(finished_at)s, %(duration_seconds)s,
                        %(total_processed)s, %(success_count)s, %(failed_count)s, %(skipped_count)s,
                        %(gemini_calls)s, %(gemini_tokens_used)s, %(estimated_cost_usd)s,
                        %(error_sample)s, %(metadata)s
                    )
                """, row)
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to write pipeline_runs row: %s", exc)
