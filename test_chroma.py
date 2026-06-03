import chromadb
client = chromadb.PersistentClient(path="data/chroma")
col = client.get_collection("songs")
info = col.count()
print(f"ChromaDB songs: {info}")