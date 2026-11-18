import sys

from omnivoice import OmniVoice, OmniVoiceGenerationConfig
import soundfile as sf
import torch

model = OmniVoice.from_pretrained(
    "k2-fsa/OmniVoice",
    device_map="cuda",
    dtype=torch.float16
)

audio = model.generate(
    text="... There's a hint of dry wit in her delivery that surfaces during lighter moments... but like most thing, it will come to a end",
    ref_audio=sys.argv[1],  # path to the reference voice clip
    ref_text="Soon you'll be healed, forgiven, and refreshed Free from all trauma, pain, guilt, and shame You may even forget your own name But before you dwell in that house forever Here's thirty minutes of easy listening",
    speed=0.8,
) # audio is a list of `np.ndarray` with shape (T,) at 24 kHz.

# If you don't want to input `ref_text` manually, you can directly omit the `ref_text`.
# The model will use Whisper ASR to auto-transcribe it.

sf.write("out.wav", audio[0], 24000)