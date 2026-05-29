import posthog
posthog.capture = lambda *a, **k: None
posthog.disabled = True

import chromadb
import config
import sys
sys.path.insert(0, '.')

client = chromadb.PersistentClient(path=config.CHROMA_PATH)
collection = client.get_collection('songs')

# Get all songs and check their metadata
results = collection.get(include=['metadatas'])
print(f'Total songs in ChromaDB: {len(results["ids"])}')

# Count how many have energy and valence
with_energy = 0
with_valence = 0
for meta in results['metadatas']:
    if 'energy' in meta and meta['energy'] is not None:
        with_energy += 1
    if 'valence' in meta and meta['valence'] is not None:
        with_valence += 1

print(f'With energy: {with_energy}')
print(f'With valence: {with_valence}')

# Check a few examples
for i in range(min(5, len(results['metadatas']))):
    print(f'Song {results["ids"][i]}: {results["metadatas"][i]}')

# Check energy/valence distribution
energies = []
valences = []
for meta in results['metadatas']:
    if 'energy' in meta and meta['energy'] is not None:
        try:
            energies.append(float(meta['energy']))
        except (ValueError, TypeError):
            pass
    if 'valence' in meta and meta['valence'] is not None:
        try:
            valences.append(float(meta['valence']))
        except (ValueError, TypeError):
            pass

if energies:
    print(f'Energy range: {min(energies):.3f} - {max(energies):.3f}')
if valences:
    print(f'Valence range: {min(valences):.3f} - {max(valences):.3f}')

# Count songs in neutral range (energy 0.3-0.6, valence 0.4-0.7)
neutral_count = 0
for meta in results['metadatas']:
    try:
        e = float(meta.get('energy', 0.5))
        v = float(meta.get('valence', 0.5))
        if 0.3 <= e <= 0.6 and 0.4 <= v <= 0.7:
            neutral_count += 1
    except (ValueError, TypeError):
        pass
print(f'Songs in neutral range (0.3-0.6 energy, 0.4-0.7 valence): {neutral_count}')
