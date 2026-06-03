import torch
import torch.nn.functional as F

from transformers import (
    Qwen2_5OmniProcessor,
    Qwen2_5OmniThinkerForConditionalGeneration,
)
from qwen_omni_utils import process_mm_info

MODEL_NAME = "LCO-Embedding/LCO-Embedding-Omni-3B-2605"
AUDIO_FILE = r"audio_files\song 45\Adib Sin - Airwaves (Ft Azuria Sky & Leopold).mp3"

processor = Qwen2_5OmniProcessor.from_pretrained(MODEL_NAME)

model = Qwen2_5OmniThinkerForConditionalGeneration.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.bfloat16,
    device_map="cuda",
).eval()

messages = [[
    {
        "role": "user",
        "content": [
            {"type": "audio", "audio": AUDIO_FILE},
            {"type": "text", "text": "\nSummarize the above audio in one word:"},
        ],
    }
]]

text = processor.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
)

audio_inputs, image_inputs, video_inputs = process_mm_info(
    messages,
    use_audio_in_video=False,
)

inputs = processor(
    text=text,
    audio=audio_inputs,
    images=image_inputs,
    videos=video_inputs,
    return_tensors="pt",
    padding=True,
)

inputs = inputs.to("cuda")

with torch.no_grad():
    outputs = model(
        **inputs,
        output_hidden_states=True,
        return_dict=True,
    )

embedding = outputs.hidden_states[-1][:, -1, :]
embedding = F.normalize(embedding.float(), p=2, dim=-1)

print("shape:", embedding.shape)
print("dtype:", embedding.dtype)
print("first values:", embedding[0, :10].cpu())