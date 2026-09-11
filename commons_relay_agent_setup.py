from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from commons_relay.agent_setup import worker_main
worker_main()
