# 🗝️ Eidolon Vault

**Persistent Scenario Intelligence Engine**  
*The secure repository for evolving agentic consciousness.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.4.1-green.svg)](https://github.com/dharanifoxx94/Eidolon-Vault)

---

## 💡 Core Concept

Eidolon Vault is a high-performance framework for orchestrating **autonomous agents** in persistent, evolving environments. It serves as a digital "vault" where agent personas and their collective memories are stored, refined, and simulated across complex scenarios.

## 🚀 Key Features

- **🧠 Cognitive Persistence**: Agents maintain history and evolve their strategies over time.
- **🎭 Persona Architecture**: Define rich, nuanced agent profiles using YAML.
- **🏠 Local-First**: 100% offline support using Ollama for maximum privacy.
- **🌐 LLM Agnostic**: Seamlessly switch between Gemini, Groq, and OpenRouter.
- **🛠️ Tool-Ready**: Built-in support for web scraping, PDF parsing, and knowledge graphs.
- **📊 Insightful Reporting**: Generates detailed post-simulation analysis and trajectory maps.

## 🛠️ Installation

```bash
# Clone the repository
git clone https://github.com/dharanifoxx94/Eidolon-Vault.git
cd Eidolon-Vault

# Install dependencies
pip install .

# For development tools (testing, linting)
pip install -e ".[dev]"
```

## 🚦 Quick Start

1. **Configure API Keys** (optional for Ollama)
   ```bash
   export GEMINI_API_KEY="your-key-here"
   # or
   export GROQ_API_KEY="your-key-here"
   ```

2. **Run a Simulation (Cloud)**
   ```bash
   eidolon-vault run
   ```

3. **Run a Simulation (Local Ollama)**
   ```bash
   # Pull a model first
   ollama pull llama3.2:3b
   
   # Run with local provider
   eidolon-vault run --provider ollama --model llama3.2:3b --text "Your scenario here"
   ```

## Local-First Mode (Ollama)

You can run eidolon-vault completely locally using [Ollama](https://ollama.com/).

First, pull a small, efficient model:

```bash
ollama pull llama3.2:3b
```

Then run eidolon-vault with Ollama as the provider:

```bash
eidolon-vault run --provider ollama --model llama3.2:3b
```

## Persistence & Memory

Agents now support **long-term memory** using a combination of **SQLite** and **ChromaDB**.

- Each agent can be initialized with a dedicated `EidolonMemory` instance.
- Interactions can be stored and later retrieved as “recent memories”.
- Memory data is stored under a local `data/` directory, so it stays on your machine.

## Demo: Consciousness Debate

 A built-in demo simulates a multi-day debate between two agents:

```bash
eidolon-vault demo consciousness
```

This will:

- Run 10 simulated “days” of discussion between an **Optimist** and a **Skeptic** agent.
- Use persistence so each agent can build on previous days’ memories.
- Generate a trajectory report at:

  ```text
  demo/trajectory_report.md
  ```

## Performance on Old Hardware

This project is designed to be usable on older laptops and desktops.

Example tested configuration:

- **Machine:** 2015 Dell E5450  
- **CPU:** Intel i5-5300U  
- **RAM:** 16GB  
- **Mode:** Offline / Local-first  
- **Models:** `gemma2:2b` or `llama3.2:3b` (via Ollama)

On this hardware, the system runs smoothly in local-first mode when using small models like `gemma2:2b` or `llama3.2:3b`.
