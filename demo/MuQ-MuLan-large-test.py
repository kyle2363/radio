import torch
import torch.nn.functional as F
from pathlib import Path
import librosa
from muq import MuQMuLan

# -----------------------------
# Config
# -----------------------------
AUDIO_DIR = r"C:\Users\kyleh\Desktop\data\projects\radio\audio_files"
TOP_K     = 5
TARGET_SR = 24_000   # MuQ-MuLan expects 24kHz (not 48k like CLAP)
CACHE     = Path("muq_embeddings.pt")

device = "cpu" #"cuda" if torch.cuda.is_available() else "cpu"

# -----------------------------
# Load MuQ-MuLan
# -----------------------------
print("Loading MuQ-MuLan...")
model = MuQMuLan.from_pretrained("OpenMuQ/MuQ-MuLan-large").to(device).eval()

# -----------------------------
# Audio loading
# -----------------------------
def load_audio(path: str) -> torch.Tensor:
    wav, _ = librosa.load(path, sr=TARGET_SR, mono=True)   # librosa resamples for you
    return torch.tensor(wav).unsqueeze(0).to(device)       # (1, T)

# -----------------------------
# Build / load index
# -----------------------------
def get_audio_files(folder: str) -> list[str]:
    return [str(p) for p in Path(folder).rglob("*.mp3")]

@torch.no_grad()
def build_index(files: list[str]) -> torch.Tensor:
    if CACHE.exists():
        print("Loading cached embeddings...")
        return torch.load(CACHE, map_location=device)

    embeddings = []
    for i, f in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {Path(f).stem}")
        wavs = load_audio(f)
        emb  = model(wavs=wavs)              # (1, D)
        emb  = F.normalize(emb, dim=-1)
        embeddings.append(emb.cpu())

    result = torch.cat(embeddings, dim=0)
    torch.save(result, CACHE)
    return result.to(device)

print("Indexing library...")
audio_files  = get_audio_files(AUDIO_DIR)[:100]
audio_embeds = build_index(audio_files)
print(f"Indexed {len(audio_files)} tracks — shape {audio_embeds.shape}")


# -----------------------------
# Text → Music search
# -----------------------------
@torch.no_grad()
def search_text(query: str):
    print(f"\n🎤  {query}")
    text_emb = F.normalize(model(texts=[query]), dim=-1)
    sims     = (text_emb @ audio_embeds.T)[0]
    for rank, i in enumerate(torch.argsort(sims, descending=True)[:TOP_K], 1):
        print(f"  {rank}. [{sims[i]:.3f}]  {Path(audio_files[i]).stem}")


# -----------------------------
# Music → Music search
# -----------------------------
@torch.no_grad()
def search_audio(seed_idx: int = 0):
    print(f"\n🎧  Seed: {Path(audio_files[seed_idx]).stem}")
    seed = audio_embeds[seed_idx:seed_idx+1]
    sims = (seed @ audio_embeds.T)[0]
    for rank, i in enumerate(torch.argsort(sims, descending=True)[1:TOP_K+1], 1):
        print(f"  {rank}. [{sims[i]:.3f}]  {Path(audio_files[i]).stem}")


# -----------------------------
# Demo
# -----------------------------
for q in [
    "sad emotional piano music",
    "heavy bass electronic dance music",
    "chill lo-fi hip hop beat",
    "acoustic indie folk song",
]:
    search_text(q)

search_audio(seed_idx=0)
search_audio(seed_idx=15)
search_audio(seed_idx=30)
search_audio(seed_idx=45)
search_audio(seed_idx=60)
search_audio(seed_idx=75)
search_audio(seed_idx=80)
search_audio(seed_idx=90)