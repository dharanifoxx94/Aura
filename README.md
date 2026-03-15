# 🌌 Aura

**Persistent Scenario Intelligence Engine**  
*Simulating complexity through multi-agent self-learning.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.4.1-green.svg)](https://github.com/dharanifoxx94/Aura)

---

## 💡 Core Concept

Aura is a lightweight yet powerful framework for orchestrating **autonomous agents** in persistent, evolving environments. Unlike static simulations, Aura agents learn from their interactions, building a collective memory that influences future scenarios.

## 🚀 Key Features

- **🧠 Cognitive Persistence**: Agents maintain history and evolve their strategies over time.
- **🎭 Persona Architecture**: Define rich, nuanced agent profiles using YAML.
- **🌐 LLM Agnostic**: Seamlessly switch between Gemini, Groq, and OpenRouter.
- **🛠️ Tool-Ready**: Built-in support for web scraping, PDF parsing, and knowledge graphs.
- **📊 Insightful Reporting**: Generates detailed post-simulation analysis and trajectory maps.

## 🛠️ Installation

```bash
# Clone the repository
git clone https://github.com/dharanifoxx94/Aura.git
cd Aura

# Install dependencies
pip install .

# For development tools (testing, linting)
pip install -e ".[dev]"
```

## 🚦 Quick Start

1. **Configure API Keys**
   ```bash
   export GEMINI_API_KEY="your-key-here"
   # or
   export GROQ_API_KEY="your-key-here"
   ```

2. **Run a Simulation**
   ```bash
   aura run
   ```

## 📂 Project Structure

- `psie/` - Core engine logic and agent controllers.
- `tests/` - Robust test suite for reliability.
- `agent_personas.yaml` - The blueprint for your simulation actors.

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for more information.

---
*Built with ❤️ for the future of agentic intelligence.*
