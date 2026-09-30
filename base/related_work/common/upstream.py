"""One import location for the proposed method's unchanged RW machinery."""
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "proposed_cache" / "core"
sys.path.insert(0, str(CORE))
import split_remote_server_reuse_score as server
import virtual_server_sim_reuse_score as virtual
from cache_extensions import register
