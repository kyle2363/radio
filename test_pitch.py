from pedalboard import PitchShift, Reverb, Limiter, time_stretch
from pedalboard._pedalboard import Pedalboard
from pedalboard.io import AudioFile


def studio_quality_stretch(
    input_file,
    output_file,
    speed_factor=0.85,
    pitch_drop_semitones=-3.0,
):
    # Read input audio
    with AudioFile(input_file) as f:
        audio = f.read(f.frames)
        sr = f.samplerate

    # High-quality time stretch
    stretched_audio = time_stretch(
        audio,
        sr,
        stretch_factor=speed_factor,
        high_quality=True,
        transient_mode="crisp",
    )

    # Effects chain
    board = Pedalboard([
        PitchShift(semitones=pitch_drop_semitones),

        Reverb(
            room_size=0.40,
            damping=0.50,
            wet_level=0.12,
            dry_level=1.00,
            width=1.00,
            freeze_mode=0.00,
        ),

        Limiter(
            threshold_db=-1.0,
        ),
    ])

    # Apply effects
    effected = board(stretched_audio, sr)

    # Save output
    num_channels = effected.shape[0]

    with AudioFile(
        output_file,
        "w",
        sr,
        num_channels=num_channels,
    ) as f:
        f.write(effected)


if __name__ == "__main__":
    studio_quality_stretch(
        input_file="C:\\Users\\kyleh\\Desktop\\data\\projects\\radio\\audio_files\\new14mp3\\skele -  i still care but i know that i shouldn't (lyrics) 「AMV」.mp3",
        output_file="track_slowed_reverb.mp3",
        speed_factor=0.85,
        pitch_drop_semitones=-3.0,
    )