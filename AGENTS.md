# AGENTS.md - Development Environment

This is a **personal home directory** with multiple projects. User prefers direct execution without excessive explanation.

## Key Projects

| Project | Location | Description |
|---------|----------|-------------|
| **Hermes Agent** | `.hermes/hermes-agent/` | Primary AI assistant |
| **IBGA** | `ibga-trading/` | Interactive Brokers Gateway |
| **Arbitrage Scanner** | `arbitrage-calculator-main/` | Prediction market arbitrage finder |
| **NeuTTS** | `neutts/` | Text-to-speech |
| **OpenCode** | `.opencode/` | OpenCode CLI |

---

# Quick Start Commands

## IB Gateway (IBGA)
```bash
cd /home/micha/ibga-trading && docker-compose up -d
# Access: http://localhost:15800
# May need 2FA on first run
```

## Arbitrage Scanner
```bash
cd /home/micha/arbitrage-calculator-main

# Venv already created at venv/ — all deps installed via uv
# Start backend (must use subshell to keep running)
(cd /home/micha/arbitrage-calculator-main && PYTHONPATH=. ./venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 &>/dev/null)

# Check status
curl http://localhost:8000/api/scan-status

# Trigger scan
curl -X POST http://localhost:8000/api/scan

# Start ngrok for Colab tunnel
~/.local/bin/ngrok http 8000 &
# or: npx @ngrok/ngrok http 8000
```

**Key endpoints:**
- `/api/scan-status` - Check if scan running
- `/api/scan` - POST to trigger scan
- `/api/markets` - Get cached markets
- `/api/opportunities` - Get found opportunities
- Auto-scan MUST stay OFF (`"auto_scan_enabled": false`)

## NeuTTS (TTS Package)
```bash
cd neutts
rm -rf venv && python3 -m venv venv
source venv/bin/activate
pip install -e ".[all]"
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
sudo apt install espeak-ng
```

**IMPORTANT**: Use GGUF models only on ARM64:
```python
from neutts import NeuTTS
import soundfile as sf, torch
tts = NeuTTS(backbone_repo='neuphonic/neutts-nano-q4-gguf', backbone_device='cpu')
ref_codes = torch.load('samples/jo.pt')
ref_text = open('samples/jo.txt').read().strip()
wav = tts.infer('Hello world.', ref_codes, ref_text)
sf.write('output.wav', wav, 24000)
aplay -D plughw:0,0 output.wav
```

## Workspace Memory
- `.hermes/memories/USER.md` - User context
- `.hermes/SOUL.md` - Assistant identity

## Important Notes
- ALWAYS backup config files before modifying
- User prefers "Plan -> Execute -> Report" workflow
- Preferred TTS voice: `en-US-ChristopherNeural`
- Uses 1Password, Telegram: `@Yourwishismycmdbot`
- Google: `bamove@gmail.com`
- Keep a record of environment variables learned from the Infisical account for later synchronization.