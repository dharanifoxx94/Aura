# PSIE: Persistent Scenario Intelligence Engine

**Version: 1.4.1**

PSIE is a multi-agent simulation engine with continuous self-learning capabilities. It allows for complex scenarios to be modeled and simulated using diverse agent personas.

## Features

- **Multi-Agent Simulation**: Robust support for parallel agent interactions.
- **Continuous Self-Learning**: Agents can evolve based on simulation outcomes.
- **Flexible Configuration**: Highly customizable simulation parameters and agent personas.
- **LLM Gateway**: Seamless integration with multiple LLM providers (Groq, Gemini, OpenRouter).
- **Knowledge Pipeline**: Automated information processing and retrieval.

## Installation

You can install the dependencies via pip:

```bash
pip install .
```

For development:

```bash
pip install -e ".[dev]"
```

## Usage

After installation, you can run PSIE directly from the command line:

```bash
psie --help
```

### Quick Start

1. Set up your API keys:
   ```bash
   export GROQ_API_KEY=your_key
   # or
   export GEMINI_API_KEY=your_key
   ```

2. Run a simulation:
   ```bash
   psie run
   ```

## Development

Run tests:
```bash
pytest
```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
