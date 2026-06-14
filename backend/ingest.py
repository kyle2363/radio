import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from ingest.pipeline import *
#results = run_ingest()            # full pipeline
results = run_phase_lyrics_timing()      # single phase
print(results)
