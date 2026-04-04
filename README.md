# Gemma Local Chat 🤖

A premium, fast, and local LLM chat interface inspired by ChatGPT, optimized for Apple Silicon using the **MLX** framework.

## Getting Started

To run this application on your Mac:

1. **Clone the repository.**
2. **Run the start script:**
   ```bash
   chmod +x start.sh stop.sh restart.sh
   ./start.sh
   ```
   *The script will automatically create a virtual environment, install necessary MLX dependencies, and initialize the database on its first run.*

3. **Access the Chat:**
   Open your browser at [http://localhost:8000](http://localhost:8000)

## 🌟 Features

- **Apple MLX Engine**: High-performance text generation with `gemma-3-4b-it-4bit-DWQ`.
- **Local & Private**: All inference and chat history stay on your Mac.
- **Voice-Enabled**:
    - **Speech-to-Text**: Click the mic to speak to Gemma.
    - **Text-to-Speech**: AI responses can be spoken aloud using the macOS `say` command.
- **Persistent History**: Conversations are saved to a SQLite database and can be resumed anytime.
- **Premium UI**: Dark-themed, glassmorphic design with smooth animations.

## 🚀 Getting Started

### 1. Requirements

- A Mac with Apple Silicon (M1, M2, M3, etc.)
- Python 3.14+ (or compatible)

### 2. Setup

If dependencies are not yet installed in your `venv`:
```bash
./venv/bin/python3 -m pip install fastapi uvicorn mlx_lm
```

Initialize the database:
```bash
./venv/bin/python3 init_db.py
```

Start and stop the FastAPI backend using the included management scripts:

```bash
# Start the server (background)
./start.sh

# Stop the server
./stop.sh

# Restart the server
./restart.sh
```

Open your browser and navigate to:
**[http://localhost:8000](http://localhost:8000)**

## 🛠️ Tech Stack

- **Backend**: FastAPI, MLX, SQLite
- **Frontend**: Vanilla HTML/CSS/JS, Lucide Icons, Google Fonts
- **Default Model**: `mlx-community/gemma-3-4b-it-4bit-DWQ`

## 🧩 Available Models

These are the some of the models that can be used:

- **Llama 3.2 1B**: `mlx-community/Llama-3.2-1B-Instruct-4bit`
- **Qwen 2.5 Coder 7B**: `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit`
- **Gemma 3 12B**: `mlx-community/gemma-3-12b-it-4bit-DWQ`

> [!TIP]
> You can switch between these models instantly using the sidebar dropdown, or add new ones by pasting their Hugging Face identifier (e.g., `mlx-community/name`).
