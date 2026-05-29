from __future__ import annotations

import sys
from pathlib import Path


def _import_archive_filename_match():
    try:
        from archive_core.filename_match import filename_token_score, score_archive_record

        return filename_token_score, score_archive_record
    except ImportError:
        skill_root = Path(__file__).resolve().parents[3] / "skills" / "archive-core"
        if skill_root.is_dir():
            root = str(skill_root)
            if root not in sys.path:
                sys.path.insert(0, root)
            from archive_core.filename_match import filename_token_score, score_archive_record

            return filename_token_score, score_archive_record
        raise


filename_token_score, score_archive_record = _import_archive_filename_match()

__all__ = ["filename_token_score", "score_archive_record"]
