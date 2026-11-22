"""Create and reconcile embeddings for associated weather documents."""

import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_embedding_batch import reconcile_pending_embeddings


logger = logging.getLogger(__name__)


def run() -> dict:
    """Run the standalone embedding reconciliation batch."""
    return reconcile_pending_embeddings()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run()
