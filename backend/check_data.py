import posthog
posthog.capture = lambda *a, **k: None
posthog.disabled = True

import chromadb

client = chromadb.PersistentClient(path='../data/chroma')
songs = client.get_collection('songs')
sample = songs.get(include=['embeddings', 'metadatas'], limit=1)
print(f'IDs: {sample["ids"]}')
print(f'Embedding dim: {len(sample["embeddings"][0])}')
print(f'Metadata keys: {list(sample["metadatas"][0].keys())}')
print(f'Sample meta: {sample["metadatas"][0]}')
