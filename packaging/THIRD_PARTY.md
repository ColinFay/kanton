# Third-party software in kanton.app

kanton itself is MIT (see LICENSE). The bundle also contains:

- **Python** — PSF License. https://www.python.org/
- **Pillow** — MIT-CMU. https://python-pillow.org/
- **pywebview** — BSD 3-Clause. https://pywebview.flowrl.com/
- **PyInstaller** bootloader — GPL with the bootloader exception that allows bundling software of any licence. https://pyinstaller.org/
- **Anton** font — SIL Open Font License 1.1 (fonts/OFL-Anton.txt).
- **faster-whisper** and **CTranslate2** (SYSTRAN) — MIT; **huggingface_hub** and **tokenizers** — Apache 2.0; **onnxruntime** — MIT,
  with the Silero VAD model (MIT); **NumPy** — BSD 3-Clause; **PyAV** — BSD 3-Clause, whose wheel carries its own FFmpeg build
  (LGPL/GPL per that build's configuration: see https://github.com/PyAV-Org/pyav-ffmpeg). The Whisper speech model
  (`large-v3-turbo`, OpenAI, MIT; CTranslate2 conversion on the Hugging Face Hub) is not in the bundle: the app downloads it
  on the first Auto captions, after asking, into `~/.cache/huggingface`.
- **FFmpeg** (`bin/ffmpeg`, `bin/ffprobe`) and the libraries in `bin/lib/` — the binaries are the Homebrew build of FFmpeg,
  configured with `--enable-gpl --enable-version3` (libx264, libx265, libvpx, libopus, libmp3lame, dav1d, SVT-AV1, libvmaf…),
  so this build is distributed under the **GNU GPL version 3**. kanton does not link FFmpeg: it runs the binaries as separate
  processes and talks to them on the command line. The FFmpeg source and the exact build recipe are available at
  https://ffmpeg.org/download.html and https://github.com/Homebrew/homebrew-core/blob/master/Formula/f/ffmpeg.rb ; the
  licence text is at https://www.gnu.org/licenses/gpl-3.0.html. Each library keeps its own licence (x264/x265 GPL, libvpx and
  opus BSD, lame LGPL…), listed in the Homebrew formulae of the same repository.

Replace the FFmpeg binaries with an LGPL-only build (no libx264/libx265, hardware H.264 through VideoToolbox) if a GPL
component is not acceptable for your distribution.
