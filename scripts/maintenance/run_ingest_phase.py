import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend"))
from ingest.pipeline import *
#results = run_ingest()            # full pipeline
results = run_phase_lyrics_timing()      # single phase
print(results)
